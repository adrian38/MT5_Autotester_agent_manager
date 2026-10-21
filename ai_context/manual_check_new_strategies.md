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
| `manager_node_runtime/manual_check.py` de **cada agente** | Copia los `.set`, escribe su `StartLots` y su `UseEveryTick`, y arma la orden de `run_tests.py`. |
| `mt5_manager/manual_check_watch.py` | Mira el nodo cada vuelta del vigilante y anuncia el final una sola vez. |
| `mt5_manager/manual_check_analysis.py` | Lee los dos informes de cada estrategia y devuelve las cinco cifras y las dos curvas. |
| `mt5_manager/portfolio_coordinator_manual_check.py` | Los métodos del coordinador, fuera de `portfolio_service.py`, que estaba en el techo de 600. |
| `static/portfolio_manual_check.js` | El modal: fechas, lote, multiterminal, los dos tipos de datos y los dos ZIP. |
| `static/manual_check_analysis.{html,js,css}` | La pestaña del análisis: OHLC M1 contra tick a tick, estrategia por estrategia. |

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

## Los sets salen con cada tick marcado (2026-10-21)

Petición del usuario: «en los sets que se van a utilizar para esto manual tiene
que tener marcado el parámetro check every tick». Es `UseEveryTick`, un input
del experto, y los sets guardados del portafolio lo traen en `false` —así los
escribe `guided_batches.py`—.

- Lo pone `_write_check_sets` del runtime del agente, que es quien escribe el
  `.set` que MT5 abre, con el mismo `_force_set_value` que ya ponía
  `StartLots`: cambia el valor y conserva el resto de la línea
  (`clave=valor||min||paso||max||optimizar`). Un set que no lo declare lo
  recibe añadido; el experto que no lo tenga lo ignora.
- El original **no se toca**: se modifica la copia de `sets/`.
- El ZIP de «Exportar estos sets» del manager sale igual
  (`portfolio_manual_check.mark_every_tick`), porque es el mismo set que la
  comprobación manda a MT5: abrirlo a mano y obtener otra cosa que el informe
  de la pantalla sería la peor forma de enterarse de que son dos ficheros.
- Lo exige la paridad, que busca `"UseEveryTick", "true"` en las tres copias.
- **La comprobación del #62 del 2026-10-21 es anterior a esto** y corrió con
  `UseEveryTick=false` en las dos etapas: sus diferencias son sólo las del
  modelo de datos del tester. Para comparar como ahora, relanzarla.

## El análisis de una comprobación (2026-10-21)

Petición del usuario: un botón que abra en otra pestaña la comparación de OHLC
contra tick a tick con las cifras del informe y un selector de estrategia.
Es `static/manual_check_analysis.html`, abierta desde «Análisis manual» del
detalle del portafolio con `?node=&check=`.

- **Las cifras son las del propio `.htm`**, no un recálculo: «Total de
  operaciones ejecutadas», «Total de transacciones», «Beneficio Neto»,
  «Beneficio Bruto» y «Reducción máxima del balance», leídas por su literal
  del informe. Recalcularlas desde las operaciones daría números parecidos y
  distintos, y nadie sabría cuál creer.
- **La curva sí se construye aquí**, porque el informe no la publica como
  serie: PnL cerrado acumulado, una parada por operación cerrada. Medido
  contra el #62, su último punto coincide con el «Beneficio Neto» del informe
  en las seis corridas.
- La diferencia se colorea sabiendo qué cifra es: en la reducción máxima subir
  es empeorar, y el número de operaciones no es ni mejor ni peor.
- **Un tipo de datos que falte se declara**, no se rellena con ceros: se pudo
  lanzar sólo uno, o el tester no dejó informe de esa corrida.
- El botón lo enciende el agente, igual que el de exportar informes: abre la
  comprobación **más reciente** de ese portafolio (`manual-check-latest`).

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

## Los ajustes son del agente y se recuerdan

Petición del usuario (2026-10-21): «estas configuraciones cuando las edito y
lanzo deberían quedar guardadas por lo menos para todo lo que tenga que ver con
comprobación manual en ese broker». Periodo, lote, multiterminal y tipos de
datos se apuntan al lanzar en `runtime/manual_check_settings.json`, **una
entrada por nodo**, y el modal siguiente de ese agente abre con ellos y lo dice.

- Van con el estado del manager y no con el portafolio: describen **cómo** se
  comprueba en ese broker, no qué lleva dentro una cartera.
- **Las estrategias no se recuerdan nunca**: se recalculan contra la memoria de
  ese momento en cada apertura y en cada lanzamiento.
- Un fichero ilegible no bloquea el modal: se vuelve a los valores de fábrica.

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

## El bloqueo de MT5 tumbaba la tanda entera (2026-10-21)

La primera comprobación con `UseEveryTick=true` falló:
`check_20261021_153823_964000` del #62 en ICTrading. OHLC M1 terminó entera en
60 s; en tick a tick, **XAUUSD H1** se colgó dos veces —1243 s y 1662 s—, el
watchdog del runner reinició MT5 una vez y al segundo bloqueo escribió
«MT5 Model=4 volvió a bloquearse tras el reintento automático». Las otras dos
estrategias ya tenían su `.htm` al lado, pero el trabajo acabó `failed` y el
aviso dijo «Comprobación manual fallida» sin más.

Es el fallo conocido de `model4_watchdog_mata_su_propia_sincronizacion.md`, que
`UseEveryTick=true` hace mucho más probable: la corrida tick a tick pasa de
segundos a decenas de minutos.

- El log dice en qué se quedó: `logs/tick/last_run.log` guarda la última línea
  del journal de MT5 antes del cuelgue.
- **2 de 3 informes estaban escritos.** Mirar `reports/<modelo>/*.htm` antes de
  dar una comprobación por perdida; la pantalla de análisis ya los enseña y
  declara el que falta.

## Reintentos, 3 por defecto y editables (2026-10-21)

Petición del usuario tras ese fallo. Son intentos **extra**: con 3, una
estrategia se prueba hasta cuatro veces.

- **Quien reintenta es el agente**, que es el único que ve el código de salida
  de cada corrida: `JobPipelineMixin._retry_manual_check` relanza la misma
  etapa antes de darla por terminada. El manager sólo manda el número.
- **Se repite sólo lo que no tiene `.htm`** (`pending_set_files`): repetir lo
  que ya salió costaría las mismas horas para reescribir lo mismo. A partir
  del segundo intento, `build_manual_check_command` recibe `attempt` y filtra
  los `--set-file`.
- **Un código de salida distinto de 0 sin informes que falten no se reintenta**:
  no hay nada que repetir, y repetir la tanda sería no entender el fallo.
- El reintento se anota en el log del trabajo («Reintento 1 de 3 en
  manual_check_tick: 1 estrategia(s) sin informe») y **no** dispara el aviso de
  etapa: un intento que se va a repetir no es el final de la etapa.
- El contador vive en el paso del pipeline como `manual_attempt`, **no** como
  `attempt`: ése ya lo usa `_step_label` para las fases de la reparación, y
  reutilizarlo habría cambiado la clave de `stage_return_codes` en cada
  intento.
- El número se recuerda por agente con el resto de ajustes
  (`REMEMBERED_KEYS`), porque describe cómo se comprueba en ese broker.

## Una comprobación fallida dejaba la cola parada (2026-10-21)

Después de aquel fallo, la siguiente comprobación se quedó en «1 PENDIENTE»
para siempre. El usuario tuvo que darle a **Detener** para que arrancara, y eso
es justo lo que señala la causa: `failed` está en `RESUMABLE_STATUSES`, así que
`_is_resumable()` daba verdadero, `_busy()` también, y `_drain_queue` volvía sin
hacer nada. `stopped` no es reanudable, y por eso detener la liberaba.

- Ahora una comprobación manual reserva el nodo **sólo mientras esté en
  pausa**, que es una acción deliberada del usuario. Fallada o interrumpida no
  hay nada que continuar: reanudarla relanzaría la etapa entera, que es lo
  mismo que volver a lanzarla desde el modal, y sus reintentos ya se agotaron.
- Es la misma excepción que ya tenía `manual_history` en esa función.
- **Una generación fallida sigue reservando el nodo**, a propósito: ahí sí hay
  algo que continuar y son horas de trabajo. El efecto secundario también
  sigue: si una generación queda `failed` o `interrupted`, lo que haya en la
  cola espera sin decir por qué. La cola no publica todavía el motivo de la
  espera.
- Los reintentos del apartado anterior hacen esto **más** probable, no menos:
  una comprobación que agota sus intentos acaba en `failed`.
