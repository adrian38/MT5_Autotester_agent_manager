# Construcción limitada a estrategias de portafolios activos

## Contrato

Portafolio UBS normal ofrece `Solo estrategias de portafolios activos` en la
creación A/M/C, en `Mejorar base` y en `Arreglar`. La clave persistida es
`active_portfolios_only`; por eso una mejora o un arreglo la heredan de la
generación anterior, la muestran con ese valor y permiten editarla antes de
iniciar el cálculo.

«Activo» no significa cualquier fila guardada. Son los usos del mismo nodo
marcados con `live_trading=true`, los mismos que publica
`LiveAuditSettingsStore.state(node_id)["live_portfolio_ids"]` y aparecen en la
pantalla `Portafolios activos`. El manager resuelve sus miembros al arrancar
cada trabajo, de modo que un formulario guardado no congela una lista vieja.
Si un paquete A/M/C tiene activo sólo un uso Moderado, entran únicamente los
miembros Moderados; no se cuelan las otras dos variantes guardadas en la misma
fila. Varios usos activos del mismo paquete aportan la unión de sus variantes.

**No es un recorte del pool aceptado: con la casilla puesta, el pool son las
estrategias en real.** Marcar una en real ya es el veredicto, así que entra
aunque nunca pasara el embudo. Su fila se construye con lo que guardó su
asignación —ruta del `.set`, símbolo, marco temporal e informes base,
robustez, Final Tick e histórico—, que es justo lo que lee
`load_robust_sets_from_rows`. En una mejora sólo limita las incorporaciones y
en un arreglo sólo las sustitutas; las estrategias originales o supervivientes
continúan protegidas por sus contratos existentes. Si el broker no tiene
portafolios activos, o ninguno de sus miembros pudo entrar, el trabajo falla
con un mensaje explícito que nombra la causa y hasta cinco de los sets.

**Un set subido a mano no guarda el par IS/OOS: se parte su pasada única.** Su
asignación trae los dos informes vacíos; lo que tiene es la pasada OHLC
completa que escribe `Generar histórico OHLC`, **un solo fichero** de 2020 a
2026, y `build_robust_strategy_set` exige dos períodos que no se solapen
—`First report period must end before second report period starts`—.

`split_report_at_year` corta ese listado de operaciones por fecha de cierre en
2025 y devuelve los dos tramos. **No inventa nada**: es el mismo listado, y
medido el 2026-11-08 sobre `strategy1_Daily` y `9637014_bk_tsla_vol_d1atr` de
AXI la unión de los dos tramos devuelve el mismo beneficio, el mismo valle, el
mismo punto y las mismas operaciones que el informe entero, al céntimo. De cada
tramo se borran el neto, los brutos y el factor del HTML para que se recalculen
desde sus operaciones; las reducciones máximas se conservan, que son las de la
pasada completa, así que el tramo hereda un riesgo que nunca se queda corto.

**Lo que no es: un fuera de muestra de verdad.** La pasada es una sola y el
tramo 2025-2026 no se reservó. El aviso del pool lo dice con esas palabras.

La ruta viaja en la fila como `single_pass_report_path`, la resuelve
`_single_pass_report` con `report_for_set` —la regla única de emparejado entre
un set manual y su histórico, que lleva el número del portafolio— y la consume
`_RobustSetLoader._candidate_report_paths`. **Una fila sin esa clave no cambia
en nada**, así que ni el embudo del agente ni ningún otro consumidor se ven
afectados.

Medido el 2026-11-08 sobre AXI: 6 usos en real, 64 miembros, 57 estrategias
distintas; 18 son candidatas aceptadas (31 filas del pool, porque el mismo
`.set` aparece hasta 11 veces regenerado en varios runs) y 39 vienen de los
portafolios manuales #109, #110 y #112. **Las 57 cargan.** Antes cargaban 18.

Un miembro en cuarentena no vuelve por esta puerta: ya salió del pool aceptado
y eso es una decisión del usuario sobre esa estrategia, no un veredicto del
embudo.

La comparación con lo que el pool ya trae es por **tronco lógico** —el nombre
sin el prefijo `robust_AAAAMM_`— y no por nombre de fichero.
`load_robust_sets_from_rows` agrupa por `account_type:tronco` y la fila de un
miembro no trae `account_type`, así que el set a mano y su copia robusta del
pool llegarían como dos estrategias distintas.

**Un uso activo puede ser un portafolio manual**, y entonces su miembro apunta
a la copia estable del nodo —`runtime/<nodo>/manual_portfolios/<uuid>/sets/`—
mientras el mismo candidato vive en `outputs/ubs_agent/…`. Por eso
`_set_identity` compara el **nombre del `.set`**, no la ruta: con rutas, medido
el 2026-11-08, los tres usos manuales de AXI no aportaban un solo candidato y
el cálculo abortaba, aunque los cuatro sets de uno de ellos están aceptados.
Entran también las filas distintas que comparten fichero —el mismo set
regenerado en varios runs—, y eso es lo buscado: lo que el usuario marcó en
real es la estrategia, no la fila que la descubrió.

**La casilla de una mejora no viaja en la raíz del cuerpo.** `Mejorar base` la
manda dentro de `improvement_inherited_overrides`, y `_merged_attempt_inputs`
sólo la mezcla en los inputs al preparar cada intento, mucho después de que el
trabajo resuelva los usos en real. Por eso `_with_active_portfolio_members`
consulta `active_portfolios_requested()`, que mira primero el heredado: con la
raíz sola, el cálculo salía con la casilla activa y sin
`_active_portfolio_members`, el filtro se quedaba sin nada con que comparar y
abortaba. Medido el 2026-11-08 sobre la mejora del #67 de AXI: 64 miembros
activos y 31 candidatos aceptados que el trabajo nunca llegó a ver. El heredado
manda también cuando viene apagado, o desmarcarlo en el diálogo haría fallar la
mejora por no haber portafolios activos. `Arreglar` manda la casilla plana, así
que nunca tuvo este problema.

## El mínimo de operaciones no se aplica

Es un criterio del embudo, y en este pool ya decidió el usuario: lo que opera
en real se usa. `_normalize_flags` fuerza `min_trades_2020_2026 = 0` cuando la
casilla está puesta, también ante una petición fabricada fuera de la interfaz,
y las tres pantallas deshabilitan el campo. Como `_merged_attempt_inputs`
normaliza **después** de mezclar los heredados, la precedencia alcanza igual a
`Mejorar base`.

Medido el 2026-11-08 sobre AXI: con el 100 por defecto se caían cuatro de sus
57 —`eurusd_h368_h4_storyg` (39 operaciones), `eurusd_f356_d1_storyg` (55),
`eurusd_p3516_d1_storyg` (77) y `ubs_spotcrude_daily_g_p006` (91)—, todas con
neto positivo y recuperación 6M en regla; son estrategias de baja frecuencia en
seis años, no estrategias malas. Con la casilla puesta son 57 de 57 elegibles.

Las otras condiciones de `filter_eligible_sets` siguen: sin curva no hay nada
que repartir, y neto negativo o recuperación 6M por debajo de 1 no eliminan a
nadie hoy —las 57 pasan— ni cambiarían el resultado, porque el optimizador
maximiza beneficio/DD y nunca elegiría una perdedora.

## Interacción con «Excluir sets usados»

Toda estrategia de un portafolio activo ya está usada por definición. Por eso
activar este filtro desmarca y bloquea visualmente `Excluir sets usados`, y el
backend fuerza el valor efectivo a `false`, también ante una petición fabricada
fuera de la interfaz. Sin esa precedencia la intersección quedaría vacía y la
casilla nueva no podría producir candidatas.

## Ejecución y alcance

El manager añade `_active_portfolio_members` sólo durante el cálculo y
`settings_inputs` lo excluye de la persistencia; únicamente se guarda el
booleano heredable. Los paths se reubican con `_resolve_source_path` antes de
compararlos con las filas del pool para no mezclar rutas del nodo, del host y
del contenedor. **No sólo el `.set`**: desde que un miembro entra al pool con
sus propios informes, `_relocated_member` reubica también `is_report_path`,
`oos_report_path`, `final_tick_report_path` y `full_history_report_path`, que
un portafolio guardado puede traer con la raíz de otro despliegue.

El cálculo y la resolución de `live_trading` viven en el manager. El nodo sólo
persiste los inputs ya serializados, así que no hay cambio de protocolo ni port
a `manager_node_runtime/`. Portafolio UBS mensual permanece congelado y no gana
esta casilla.
