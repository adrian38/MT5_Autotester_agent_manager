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

La restricción se aplica al pool de candidatas aceptadas. En una mejora sólo
limita las incorporaciones y en un arreglo sólo las sustitutas; las estrategias
originales o supervivientes continúan protegidas por sus contratos existentes.
Si el broker no tiene portafolios activos, o ninguno de sus miembros sigue en
el pool aceptado, el trabajo falla con un mensaje explícito.

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
del contenedor.

El cálculo y la resolución de `live_trading` viven en el manager. El nodo sólo
persiste los inputs ya serializados, así que no hay cambio de protocolo ni port
a `manager_node_runtime/`. Portafolio UBS mensual permanece congelado y no gana
esta casilla.
