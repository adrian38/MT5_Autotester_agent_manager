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
