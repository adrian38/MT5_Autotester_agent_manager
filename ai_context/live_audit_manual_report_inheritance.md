# El reporte manual sobrevive a la ejecución siguiente

Lo único que descarta un reporte manual es que la ejecución nueva acredite el
100 % de calidad por su cuenta. Mientras el backtest siga saliendo con el
historial de ticks incompleto, la corrección que el usuario aportó una vez se
vuelve a aplicar sola.

## Qué pasaba

La noche del 19 al 20 de octubre de 2026 el cron ejecutó por primera vez de
verdad y rehizo `audit-105-mv4perus-1` de AXI. Las tres estrategias de XAUUSD
volvieron a salir al 40 % y la pantalla pidió otra vez los tres HTML que ya se
habían subido el 13 y el 14. En el disco del agente se ve entero:

- `audit_audit-105-mv4perus-1/20261012_235737_934178/reports/` tiene los trece
  `audit_0NN_*.htm` automáticos **y tres `manual_*.htm`**.
- `.../20261020_003045_544822/reports/` sólo tiene los automáticos.

No fallaba la carga: no había carga. Nadie reaprovechaba `report_source =
manual_upload` entre ejecuciones, y hasta que existió el cron daba igual porque
re-ejecutar era un clic del usuario.

## Por qué la copia la guarda el manager

El nodo guarda **un** payload por uso y, además, `artifact_path()` sólo sirve
artefactos de la **ejecución visible**: en cuanto el cron ejecuta otra vez, el
`manual_*.htm` de la anterior ya no se puede ni pedir por HTTP. Por eso el
manager se queda con su propia copia al reenviar la carga —`ManualReportMemory`,
en `runtime/live_audit_manual_reports/`—, igual que el diario de ejecuciones
guarda lo que ya no cabe en el nodo.

**No hay nada que portar al agente.** La herencia reenvía el HTML guardado al
mismo endpoint que usa el navegador, así que el nodo lo valida como cualquier
carga manual: nombre exacto del `.set`, símbolo, timeframe y History Quality
100 %.

## La ventana no tiene que ser la misma: se aterriza en operaciones

Lo que se compara son operaciones, no rangos. Por eso el análisis se queda con
**las operaciones del tester que pertenecen a la ventana auditada** —las que
cerraron dentro, más las que seguían abiertas al final, que es exactamente lo
que trae el lado real— y con eso un backtest **más ancho** que la ventana sirve
igual. El usuario puede lanzar un backtest largo una vez y que aguante las citas
siguientes del cron en lugar de rehacerlo cada noche.

`tester_trades_in_window` no cambia nada de lo que ya había: con el reporte que
ejecuta el nodo, MT5 corre justo la ventana. Medido sobre los `state.json` de
los tres agentes el 2026-10-20: 444 operaciones en 11 ejecuciones, **ninguna**
fuera de su ventana, ni por cierre ni por apertura, y ninguna ficha cambia de
número.

Lo que sí se comprueba antes de heredar es que el backtest guardado **llegue a
esas fechas** (`covers_window`, con `backtest_start`/`backtest_end` leídos del
propio HTML, que no son `period_start`: ése pasa a ser la primera operación en
cuanto hay alguna). Un reporte que no las cubre no aporta ninguna operación, y
ahí pedirlo otra vez es lo correcto: la ficha lleva `manual_report_other_window`
y la pantalla dice hasta dónde llega el que hay guardado.

## Dónde está

| Pieza | Qué hace |
| --- | --- |
| `mt5_manager/live_audit_manual_memory.py` | La copia del HTML, su SHA-256 y la ventana. Una entrada por `nodo/uso/.set`. |
| `mt5_manager/live_audit_manual_inherit.py` | Decide y reenvía, en cargas que el nodo pueda aceptar. Tres intentos por ejecución y proceso. |
| `live_audit_analysis.tester_trades_in_window` | Recorta el lado tester a la ventana auditada: es lo que hace válido un backtest más ancho. |
| `live_audit_executions.analysed_node_state` | El único sitio donde se engancha: así heredan igual la pantalla y el vigilante del cron. |
| `manager_live_audit_routes._remember_manual_reports` | Guarda la copia cuando la carga pasa por el manager. |

Que la herencia viva en `analysed_node_state` no es casual: si sólo la hiciera
la pantalla, el archivado del vigilante guardaría en la serie histórica un
veredicto peor que el real, que es la misma trampa que ya costó los usos con
autolote.

## Dónde está el almacén de verdad

`runtime/runtime/live_audit_manual_reports/`, **con `runtime` dos veces**. Todo
lo de la auditoría en vivo cuelga de `live_audit_settings.json`, y en el
contenedor el manager arranca con `--config /app/runtime/manager.docker.json`
trabajando desde ahí, así que `Path.cwd()/"runtime"/...` cae un nivel más
abajo. Al lado están `live_audit_settings.json`, `live_audit_history/`,
`live_audit_credentials.json` y el estado del cron; los `live_audit_*.json`
sueltos en `runtime/` son restos viejos que ya no lee nadie.

El 2026-10-20 se perdió una hora por esto: los tres HTML del uso 105 se
sembraron en `runtime/live_audit_manual_reports/`, el almacén del servidor
quedó vacío y la herencia no hacía nada **sin decir nada** -no encontrar ficha
es el caso normal, no se registra-. Si la herencia no salta, lo primero es
mirar `index.json` **en la carpeta anidada**.

## Cargas anteriores al almacén

La memoria empieza vacía al desplegar este cambio, pero el usuario no tiene que
volver a subir lo que el nodo ya había aceptado. Si falta una ficha, el manager
busca el `manual_<set>.htm` de las ejecuciones anteriores bajo el proyecto del
nodo, comprueba que su backtest cubra la ventana visible y lo reenvía por el
mismo endpoint. Sólo después de que el nodo vuelva a validarlo lo incorpora a
su almacén persistente.

Esto funciona tanto dentro del contenedor (`portfolio_project_dir`, por ejemplo
`/data/axi`) como ejecutando el manager en el host (`node_project_dir`). No se
porta al runtime: el nodo sigue siendo quien valida símbolo, timeframe y calidad.

Ese barrido se hace **por estrategia pendiente y en cada refresco de la
portada**, sobre un bind mount donde cada `stat` cuesta 3,4 ms: busca dos
nombres, así que filtra por nombre y sólo pregunta por los que encajan. Al
revés costaba 22 de los 24 segundos que tardaba la tarjeta de AXI —
`manager_bind_mount_stat_cost.md`.

## Para la próxima

- El umbral es el del nodo (99,995 %), no el `min_tick_history_quality_pct` del
  perfil, que es otra cosa: aquél decide si se puede sustituir, éste si el
  veredicto avisa.
