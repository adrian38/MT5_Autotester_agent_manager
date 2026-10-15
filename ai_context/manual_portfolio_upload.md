# Solo portafolio desde una carpeta de sets

## Contrato

La tarjeta del nodo ofrece `Solo portafolio`, una pantalla independiente de
Portafolio UBS, mensual y Grid. El navegador carga una carpeta mediante
`webkitdirectory`; sólo transporta los `.set` y limita el lote a 35 MB.

No se importan ni se inventan informes o métricas. Al seleccionar la carpeta,
el navegador lee UTF-8 o UTF-16 y muestra `ForceSymbol`, `TimeFrame` y
`StartLots` por estrategia. Símbolo y timeframe son editables; si falta el
timeframe intenta inferirlo del nombre (`ScalpH1_C.set` → `H1`). Antes de subir,
normaliza ambos campos dentro de una copia UTF-16 del set, sin cambiar el
original del usuario. Cada set debe conservar un `StartLots` positivo. La composición se guarda
con una unidad por fichero y el lote exacto de `StartLots`; beneficio y DD
quedan a cero con un aviso explícito de histórico pendiente.

## Quién persiste

El manager reenvía el lote a `POST /api/v1/portfolios/manual`. El runtime real
del nodo guarda las copias estables bajo
`runtime/<node>/manual_portfolios/<uuid>/sets/` y registra las asignaciones en
su memoria UBS local. Guardar los ficheros sólo en el manager no serviría: la
auditoría se ejecuta en el agente y tiene que poder resolver `set_path` allí.

Los nombres de fichero deben ser únicos aunque procedan de subcarpetas, porque
los reportes manuales y los artefactos de auditoría identifican cada estrategia
por el nombre del set.

## Histórico y auditorías

Después del guardado, la pantalla ofrece primero `Generar histórico OHLC`. Las
fechas inicial y final se leen de `FromDate` y `ToDate` del `tester_template.ini`
del nodo, se pueden editar y se validan como `YYYY.MM.DD`. La ejecución fuerza
`Model=1`, usa los sets persistidos de ese portafolio y entra en la misma cola
exclusiva que los demás trabajos del agente.

`Auditorías` abre `live_audit.html` con el id del portafolio. La pantalla
reconoce el parámetro `portfolio`, marca esa cartera y crea o recupera su uso de
auditoría. El modo elegido al crearla es una sola variante (`aggressive`,
`balanced` o `conservative`) y no se vuelve a elegir.

El botón no ejecuta MT5 ni inicia una auditoría: deja al usuario en la pantalla
de configuración, donde siguen siendo obligatorias las cuentas, credenciales y
el periodo.

## Inventario separado y progreso

Las filas manuales conservan `metrics.inputs.manual_portfolio=true`. El listado
general proyecta esa marca: `Portafolio UBS` excluye esas filas y
`Portafolios manuales` muestra sólo ellas. Auditorías no aplica el filtro porque
es el inventario común de ambos orígenes.

El histórico sí se ejecuta en el nodo como trabajo `manual_history`. La pantalla
de creación y el inventario manual sondean `/api/nodes` y el log del nodo para
mostrar cola, ejecución, finalización o fallo. Los hitos del log sólo aproximan
el porcentaje durante MT5; el estado `completed` es el único que marca 100 %.

Las dos pantallas permiten elegir entre el multiterminal configurado y la
terminal principal. El nodo conserva la elección en la petición del trabajo:
multiterminal usa el límite de workers configurado; terminal única fuerza un
worker. En ambos casos el runtime añade el mapa y los sufijos del broker al
comando de `run_tests.py`, por ejemplo `.sa` para los símbolos AXI.

En el inventario, el selector siempre queda visible junto a la acción del
histórico. Las fechas y el progreso se despliegan inmediatamente debajo de esa
barra, antes de las métricas y tablas; nunca al final del detalle, porque allí
parece que el botón no respondió. El progreso usa los eventos reales de jobs
del nodo para mostrar completados, activos y pendientes.

Cada lanzamiento queda ligado al `job_id` que devuelve el nodo o, si entra en
cola, a una fecha de solicitud mínima. El sondeo ignora estados cacheados y el
último trabajo anterior del mismo portafolio: de otro modo un fallo viejo se
puede presentar como resultado instantáneo del intento nuevo. Al terminar sólo
se refrescan las métricas y tablas; no se oculta ni se reconstruye el panel de
ejecución.

Si el nodo devuelve `queued`, la pantalla conserva el id de `queue_item` y
muestra su posición real. El runtime considera el histórico manual un lote de
una sola etapa, no resumible tras fallo, y compacta solicitudes repetidas del
mismo portafolio para que un error anterior no bloquee la cola ni un doble clic
duplique los backtests.

El inventario no es una colección de enlaces: replica el patrón maestro-detalle
de UBS. Al seleccionar una cartera consulta su detalle, relaciona cada `.set`
con el HTML homónimo de `<proyecto>/reports` y muestra composición, periodo,
operaciones, beneficio, drawdown, acierto, profit factor, calidad y desglose
mensual. La lectura usa la caché por mtime/tamaño; no recorre el directorio de
reportes, que puede contener miles de artefactos.

Mientras esa consulta prepara el detalle y sus tablas, el panel muestra un
indicador de carga. Si la petición falla, el indicador se sustituye por un
mensaje persistente en el propio panel además del aviso temporal.

El detalle manual no reconstruye `inherited_inputs`: esa normalización exige
capital y límites DD de una construcción UBS, datos que deliberadamente no
existen en una carpeta de sets. Aplicarla deja el panel vacío con «Capital y DD
valle deben ser mayores que 0» aunque el histórico sea válido.

## Importar temporalidades desde Word (2026-10-15)

La pantalla de creación admite un DOCX con una tabla que contenga las columnas
`Nombre del archivo` y `Temporalidad`. El manager lee directamente el XML del
documento, admite nombres de reporte `.htm`/`.html` y los relaciona con los
`.set` por su nombre base sin distinguir mayúsculas. Sólo acepta temporalidades
MT5 conocidas y rechaza asignaciones contradictorias para el mismo nombre.

El documento se puede cargar antes o después de la carpeta: la interfaz conserva
el mapa, aplica las coincidencias y muestra cuántos sets cambió y cuántas filas
no encontraron set. La temporalidad importada sigue siendo editable antes de
crear el portafolio. Esto vive sólo en el manager; el nodo recibe los `.set` ya
normalizados por el flujo existente y no necesita un cambio de runtime.

## Detalle por estrategia y combinado (2026-10-15)

El inventario manual conserva como vista inicial `Detalle por estrategia`: la
composición y todas las tablas mensuales siguen sin cambios. `Detalle del
portafolio` suma por año y mes los resultados de todos los reportes disponibles
y añade el total anual. El intervalo mostrado abarca desde el inicio más antiguo
hasta el final más reciente de los históricos combinados.

Es una proyección de los mismos datos ya leídos por el manager: no vuelve a
ejecutar MT5, no altera los reportes y no suma drawdowns individuales. El DD
combinado de la cabecera conserva su cálculo cronológico sobre todas las
operaciones cerradas.
