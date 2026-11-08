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

**Sin informe base y de robustez no hay fila que construir.** Un set subido a
mano a un portafolio manual no guarda ninguno de los dos: su único histórico es
la pasada OHLC completa que escribe `Generar histórico OHLC`, **un solo
informe** de 2020 a 2026, y `build_robust_strategy_set` exige dos períodos que
no se solapen —`First report period must end before second report period
starts`—. Partir esa pasada en dos inventaría un fuera de muestra que nunca lo
fue, así que esos miembros se nombran en un aviso en vez de desaparecer.
Medido el 2026-11-08 sobre AXI: 6 usos en real, 64 miembros, 31 filas del pool
aceptado y 39 nombrados —los 32 de #109, los 7 de #110—, todos con histórico
OHLC y ninguno con el par IS/OOS. Para que entren de verdad hay que correrles
las dos etapas, no relajar la carga.

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
