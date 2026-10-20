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

## La ventana manda

El nodo no mira fechas al aceptar un reporte, así que la comprobación vive en el
manager: se hereda **sólo si la ventana auditada es la misma**. Un backtest del
1 al 12 de octubre no contiene ni una operación del 12 al 20; aplicarlo ahí no
conservaría trabajo, compararía otros días y el veredicto saldría falso.

Con el encadenado del cron ya arreglado cada noche audita una ventana nueva, así
que ese caso va a ser el normal: cuando no se puede heredar, la ficha de la
estrategia lleva `manual_report_other_window` y la pantalla dice qué ventana
cubre el reporte guardado en vez de pedir el archivo a secas.

## Dónde está

| Pieza | Qué hace |
| --- | --- |
| `mt5_manager/live_audit_manual_memory.py` | La copia del HTML, su SHA-256 y la ventana. Una entrada por `nodo/uso/.set`. |
| `mt5_manager/live_audit_manual_inherit.py` | Decide y reenvía. Tres intentos por ejecución y proceso. |
| `live_audit_executions.analysed_node_state` | El único sitio donde se engancha: así heredan igual la pantalla y el vigilante del cron. |
| `manager_live_audit_routes._remember_manual_reports` | Guarda la copia cuando la carga pasa por el manager. |

Que la herencia viva en `analysed_node_state` no es casual: si sólo la hiciera
la pantalla, el archivado del vigilante guardaría en la serie histórica un
veredicto peor que el real, que es la misma trampa que ya costó los usos con
autolote.

## Para la próxima

- La memoria empieza vacía: una corrección anterior a este cambio no está
  guardada en el manager y hay que volver a subirla **una vez**.
- El umbral es el del nodo (99,995 %), no el `min_tick_history_quality_pct` del
  perfil, que es otra cosa: aquél decide si se puede sustituir, éste si el
  veredicto avisa.
