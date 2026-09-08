# El lote mínimo pertenece al broker, no al perfil de margen

## Fallo confirmado el 2026-09-05

La construcción UBS del manager permitía elegir el perfil financiero TTP sobre
candidatos ICTrading. `build_margin_model` salía antes de leer las especificaciones
si el perfil no era AXI o ICTrading. Así se perdía `volume_min`, aunque el fichero
del agente sí lo publicaba: DE40 con tres unidades terminaba en 0.03 lotes en vez
de 0.30 (mínimo 0.10); US30 con dos unidades, en 0.02 en vez de 0.20.

## Regla y reparación

`PortfolioSource.symbol_specs` selecciona el fichero del broker de origen.
`build_margin_model` debe cargar sus mínimos con cualquier perfil financiero,
incluido TTP. Solo el perfil AXI incorpora además margen medido, nocional y
apalancamiento de cuenta. No forzar el selector a ICTrading: el usuario puede
elegir TTP y debe conservar su política de margen.

El modelo viaja por `_optimizer_kwargs` al optimizador compartido de UBS completo
y mensual (también Grid y mejora de base). El mensual conserva su orquestación,
recorte y estado de disponibilidad. El redondeo ejecutable y la reevaluación de DD
existentes siguen aplicándose; no basta con multiplicar el lote visible de una
cartera guardada ni con elevarlo al mínimo después del cálculo.

## Qué proceso ejecuta el cambio

La generación solicitada ocurre en el proceso web del **manager**, en
`mt5_manager/portfolio_service.py`. El nodo ICTrading recibe las asignaciones ya
calculadas: `manager_node_runtime/portfolio_save.py::_deserialize_proposals`
conserva `units`, `lot` y `lot_size_step`; no reoptimiza. Este defecto no requiere
portar el constructor a ese fork. La construcción de la interfaz local del agente
es otro flujo.

Hay que recargar el proceso del manager para aplicar el código y recalcular las
carteras afectadas. La corrección no modifica memorias ni portafolios guardados.

## Verificación

`tests/test_portfolio_margin_profiles.py` comprueba ICTrading con los perfiles
ICTrading, TTP y RoboForex, en ambos scopes, y la traducción a pasos del EA para
mínimos 0.01, 0.10 y 1.00. Comprueba también que margen y nocional medidos de AXI
no se activen al elegir otro perfil.

La regla es general para todos los brokers. La prueba
`test_every_broker_keeps_its_minimum_lot_under_every_margin_profile` cruza
ICTrading, AXI y RoboForex con los cuatro perfiles (incluido TTP). Publica mínimos
distintos para el mismo símbolo y verifica que el lote y el paso exportado usen
siempre el del broker de origen. Estas pruebas usan ficheros temporales, no
modifican ninguna copia de producción.

## El tamaño de contrato y el nocional también son del broker (2026-09-08)

El mismo reparto mal hecho, un campo más abajo. `volume_min` ya viajaba con
cualquier perfil; `contract_size` y `notional_min_lot` seguían reservados a AXI,
así que TTP sobre candidatos ICTrading calculaba el nocional como
`lote x 1 x precio`. Y el tramo de margen se había perdido por otra vía: vivía
sólo en `margin_leverage_for_profile`, y cuando el cálculo pasó a `MarginModel`
el perfil TTP se quedó en `default_leverage`, 1:500 para todo.

Las dos cosas juntas, medidas en el portafolio **#27**:

| Concepto | Antes | Con el arreglo |
| --- | --- | --- |
| Margen total | 51,29 · **1,0%** de 5.000 | 4.377,14 · **87,5%** |
| GBPUSD 0,22 lotes | contract_size 1, 1:500 | nocional medido 25.742, 1:50 |
| USDJPY 3 unidades | 1:500 | 1:50, nocional medido 2.598 |

El aviso al usuario prometía la tabla publicada —«Forex 1:50; indices 1:15;
commodities/metales/energias 1:10; stocks/crypto 1:2»— mientras el cálculo usaba
1:500 y contract_size 1. Con `validate_margin: true` y `max_margin_pct: 100`, el
#27 pasó la validación con un 1,0% declarado.

Tres correcciones:

1. **`ttp_leverage_for`** es ahora la única implementación del tramo.
   `margin_leverage_for_profile` y `MarginModel.leverage_for` la llaman, así que
   no puede volver a existir en un sitio y faltar en el otro. En TTP el tramo ES
   el requisito: no lo recortan el tope del producto ni el apalancamiento de la
   cuenta de origen.
2. **`contract_size` y `notional_min_lot` viajan con cualquier perfil**, porque
   son del instrumento. El margen medido (`margin_min_lot`) **no**: lleva dentro
   los tramos y el apalancamiento del broker que lo midió, así que sólo vale
   para su propio perfil. Corrige el alcance de la frase anterior de este
   documento: lo que no se activa con otro perfil es el margen medido, no el
   nocional.
3. **El aviso se redacta con los tramos aplicados**, no con un texto paralelo.
   `portfolio_margin_summary` publica `group_leverage_applied` y
   `contract_size_measured`, y el aviso los imprime.

Trampa que apareció al arreglarlo: con el tamaño de contrato real,
`lote x contrato x precio` se descuadra por el tipo de cambio en todo lo que no
cotice en la divisa de la cuenta. USDJPY con 0,03 lotes daba 485.031 de nocional
donde son 2.598, y ese solo símbolo llevaba el total del #27 al 309%. Por eso se
usa `notional_min_lot`, que el volcado publica ya convertido —lo dice su propio
`notional_note`—, con la estimación por precio como respaldo. El volcado de este
equipo declara `account_currency: EUR`, así que el 87,5% está medido en euros,
igual que el capital.

A cambio, el nocional medido usa el precio del día del volcado (17-08) y no el
máximo visto en los reportes, que es más conservador: en XAUUSD son 3.822 frente
a 5.247. Manda el medido, igual que en AXI: el error de divisa era de un orden
de magnitud y el de precio es del 15%.

Verificación: `python -m unittest discover -s tests` en verde, 482 pruebas.
Cuatro regresiones nuevas en `tests/test_portfolio_margin_profiles.py`, validadas
por mutación (quitar la rama TTP devuelve 1:500; no propagar el contract_size
deja 1,0 en los tres perfiles no-AXI y los dos ámbitos; sin nocional medido el
margen de USDJPY se multiplica por 186). El cálculo corre en el manager Docker:
**no surte efecto hasta reconstruir la imagen**.
