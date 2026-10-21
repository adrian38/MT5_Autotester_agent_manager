# Comprobación manual de las estrategias nuevas de un portafolio

Petición del usuario (2026-10-21): un botón en el detalle del portafolio que
mande a generar «el reporte de las nuevas estrategias incluidas en el
portafolio», con su modal de fechas, multiterminal, OHLC y tick a tick, un
botón para exportar esos sets, las imágenes de los gráficos dentro de lo
exportado y un aviso push al terminar.

## Qué es «nueva»

Un portafolio puede ser generación, mejora o arreglo. En los dos últimos casi
todo viene heredado y **ya tiene informes guardados**. Nueva es la estrategia
que no está en **ningún** antecesor: `improvement_origin.source_id`,
`root_id` y toda la `lineage`. Sin antecesores —una generación— son todas.

- La identidad entre portafolios es el `candidate_id`; el nombre del `.set` es
  el respaldo para los portafolios manuales, que no lo traen.
- Un antecesor que ya no se puede leer **no se da por vacío**: sale en
  `unreadable_origins` y la pantalla lo dice. Darlo por vacío mandaría a MT5
  estrategias que ya tenían informe.
- Los miembros excluidos no se comprueban.
- Las estrategias no se toman del navegador al lanzar: se recalculan en el
  manager con la memoria de ese momento (`start_request`), porque la pantalla
  pudo quedarse abierta mientras el portafolio cambiaba.

## Quién ejecuta qué

| Dónde | Qué |
| --- | --- |
| `mt5_manager/portfolio_manual_check.py` | Decide qué se comprueba, arma la petición, lee del disco lo que quedó y hace los dos ZIP. |
| `mt5_manager/manager_portfolio_routes.py` | `manual-check-preview`, `-start`, `-state`, `-download`, `-sets-download`. El `-start` es un reenvío. |
| `manager_node_runtime/manual_check.py` de **cada agente** | Copia los `.set`, escribe su `StartLots` y arma la orden de `run_tests.py`. |
| `mt5_manager/manual_check_watch.py` | Mira el nodo cada vuelta del vigilante y anuncia el final una sola vez. |
| `static/portfolio_manual_check.js` | El modal: fechas, lote, multiterminal, los dos tipos de datos y los dos ZIP. |

**Esto no lo ejecuta el manager**: lanzar MT5 es del agente, así que vive en
`manager_node_runtime/` y hay tres copias (IC, AXI, RoboForex). FUTURE está
congelado y queda fuera. Lo exige
`tests/test_node_runtime_fork_parity.py::test_manual_check_reaches_every_reachable_fork`.

## Dos ejecuciones, no una

El Strategy Tester acepta **un** modelo de datos por corrida, así que OHLC M1
(`--model 1`) y tick a tick (`--model 4`) son dos etapas del mismo trabajo y
dejan dos informes por estrategia. Los dos números son contrato: la paridad
los comprueba literalmente, porque si una copia los cambia el mismo botón mide
cosas distintas en cada agente. Qué significa esa diferencia, en
`exclusion_verdict.md`.

## Las carpetas del runner se reapuntan

Igual que el auditor real: el envoltorio `-c` pone `REPORT_DIR`, `CONFIG_DIR` y
`LOG_DIR` de **todos** los módulos `run_tests_*` en
`runtime/<nodo>/manual_checks/<check_id>/`. Sin eso los informes caen en
`reports/` del proyecto, se mezclan con los de la generación y la limpieza
histórica se los lleva. Reapuntar sólo la fachada no basta:
`live_audit_runner_dirs_after_split.md`.

## Las imágenes van con el informe

MT5 escribe `<informe>-hst.png`, `-holding.png` y `-mfemae.png` al lado del
`.htm`, y el HTML las referencia. El ZIP de la comprobación se lleva la carpeta
entera, así que el informe exportado abre con sus gráficas. El ZIP de informes
de un miembro guardado (`portfolio_member_reports.md`) sigue llevando sólo los
HTML: ésa es otra ruta y no se ha tocado.

## Decisiones que no se deducen del código

- **El lote por defecto es 0,01 y es editable en el modal.** El informe sale
  comparable con los que el portafolio ya guarda, medidos todos a lote mínimo.
  No es el lote del portafolio: eso es lo que mide el Auditor real.
- **Las terminales son las del pipeline del agente**, no las del Auditor real:
  no hace falta ninguna credencial y la casilla sólo decide si reparte entre
  terminales (`Multiterminal.workers`) o usa una.
- **El área de trabajo se prepara al pedir, aunque la tarea acabe encolada.**
  Un `.set` que ya no está en disco tiene que fallar delante del usuario, no
  tres horas después dentro de la cola.
- **El aviso se mira desde el manager.** Aquí el trabajo corre en el agente, así
  que no hay hilo propio al que colgarle el aviso como en
  `portfolio_job_bell_notices.md`: `manual_check_watch.tick` lee el estado de
  cada nodo en la vuelta del vigilante (cinco minutos) y recuerda en
  `runtime/manual_check_notices.json` lo ya anunciado. Sin esa memoria el
  mismo final se anunciaría en cada vuelta, porque el nodo conserva su último
  estado hasta que arranca otro trabajo.
- **El progreso va en la chapa de la cabecera**, la misma que ya contaba las
  auditorías (`#cron-activity`): es la misma espera de horas en el mismo
  agente, y dos indicadores en la cabecera se tapan el uno al otro. El
  porcentaje son corridas del tester terminadas sobre corridas del trabajo
  —«OHLC M1 · 1 de 2»—: el runner no publica nada más fino, y vale más
  contar etapas que inventar un porcentaje dentro de una.
- **Exportar los informes es un botón de la barra del detalle**, deshabilitado
  mientras ese portafolio no tenga ninguna comprobación con informes. Quién lo
  habilita es el agente (`manual-check-latest` busca su carpeta más reciente),
  no el navegador: así una comprobación lanzada desde el móvil se exporta desde
  el PC, y una carpeta borrada en el agente lo apaga en todos los dispositivos.
  Se lleva el `.htm` **y las imágenes de sus curvas**.

## El primer aviso real no salió (2026-10-21)

La primera comprobación de verdad —portafolio #62, tres estrategias, los dos
tipos de datos— terminó bien y **no anunció nada**. `tick` pedía
`server.live_audit_settings_path`, que es una variable local del `__init__` del
servidor y no un atributo suyo: el `AttributeError` moría en el `try` del bucle
y dejaba una sola línea en stderr.

- El diario cuelga ahora de `live_audit_scheduler_path`, que sí es atributo.
- Las pruebas pasaban porque el doble de prueba tenía el atributo inventado.
  **Un doble no demuestra que el servidor lo tenga**: lo comprueba
  `ServerSurfaceTests`, que cruza por AST los `server.<algo>` del vigilante con
  los `self.<algo> =` de `ManagerServer`.
- Mirar primero `docker logs mt5-autotester-manager | grep -a manual-check`: el
  vigilante no puede romper la vuelta del auditor, así que sus fallos sólo
  viven ahí.

## El aviso llega hasta cinco minutos tarde, y sólo del trabajo actual

La vuelta del vigilante es de 300 s. Para un trabajo de horas da igual, pero el
nodo sólo publica **su trabajo actual**: si se lanza otro antes de que pase esa
vuelta, la comprobación terminada deja de verse y no se anuncia. La carpeta y
sus informes siguen ahí; lo único que se pierde es el aviso.

## Lo que ya se ha ejecutado de verdad

La comprobación del portafolio #62 en ICTrading (2026-10-21): 3 estrategias
nuevas, OHLC M1 y tick a tick, `return_code` 0 en las dos corridas. Dejó en
`runtime/ictrading-standard-test/manual_checks/check_20261021_135246_528000/`
seis informes y sus cuatro imágenes cada uno —balance, `-hst`, `-mfemae` y
`-holding`—, que el `.htm` referencia por nombre relativo: por eso el ZIP las
guarda en la misma carpeta que el informe. Los `*.mt5log.txt` que MT5 copia al
lado de cada uno van en `journals/` dentro del ZIP.

AXI y RoboForex tienen el mismo port pero no se ha lanzado ninguna ahí.
