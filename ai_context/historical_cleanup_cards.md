# Limpieza histórica desde las tarjetas

- El botón del agente ejecuta, en este orden, `scripts/cleanOldTest.ps1` y
  `scripts/cleanOlddata.ps1`.
- La operación cierra MetaTrader, MetaTester y MetaEditor y elimina datos de
  `tester`, `bases`, `history`, los reportes y la caché del Tester **de las
  terminales de ese agente, y de ninguna otra**. No elimina los reportes
  locales del proyecto usados por UBS.
- El nodo expone la capacidad `historical_cleanup` solamente cuando encuentra
  ambos scripts. El manager muestra el botón manual y la opción automática
  únicamente en ese caso.
- La limpieza manual usa `POST /api/nodes/<id>/cleanup`, que el manager reenvía
  a `POST /api/v1/jobs/cleanup`. Es una tarea encolable y requiere confirmación
  destructiva en el navegador.
- Una generación añade `cleanup_tester`, `cleanup_data` y `cleanup_verify` al
  final de cada ciclo cuando `cleanup_after_run` está activo. Su valor
  predeterminado es activo siempre que ambos scripts estén disponibles.
- Si un script de limpieza falla, el nodo intenta igualmente los pasos de
  limpieza restantes, marca el job como fallido y no comienza el ciclo siguiente.
- `cleanup_verify` comprueba que no queden archivos en los árboles históricos
  **de esas mismas carpetas**: `tester`, `bases`, `history` de cada una y su
  `MetaQuotes\Tester\<id>`.

## Cada agente limpia sólo sus terminales (2026-11-17)

Hasta ese día el criterio de las tres etapas era `%APPDATA%\MetaQuotes\Terminal`
entero. Los cuatro agentes de este equipo comparten máquina y dos de ellos
(ICTrading y AXI) el mismo usuario de Windows, así que cada limpieza entraba en
las terminales de los demás. El 17 la de IC borró la caché del Tester de AXI, no
pudo vaciar sus `bases` —AXI los tenía abiertos desde otra sesión de Windows— y
`cleanup_verify` dejó la tarjeta de **ICTrading** en `FAILED` por lo que había
abierto **AXI**.

- Las carpetas salen ahora de `cleanup_terminal_targets`, que lee las secciones
  `Terminal.*` de `ui_settings.ini` cuyo `broker` es el del nodo. Las
  deshabilitadas entran: son suyas y nadie más las limpia.
- `[Paths] mt5_path` **no** sirve de respaldo: en el `ui_settings.ini` de IC
  apunta al terminal de RoboForex. Tampoco el `[Terminal.1]` que IC y AXI
  arrastran con `broker=ROBOFOREX`.
- Sin ninguna terminal del broker, `build_historical_cleanup_command` levanta
  `ValueError`. Una lista vacía que llegara a los scripts sería el fallo de
  antes otra vez, y encima en verde.
- El nodo se las pasa a PowerShell en `-DataDirs` y `-TerminalPaths`, separadas
  por `|`, y al verificador como `sys.argv[1:]`. Con `-File`, PowerShell no
  parte una cadena por espacios ni por comas: por eso el separador es `|`, que
  además es ilegal en una ruta de Windows.
- Los scripts sólo cierran los procesos cuyo `Path` cuelga de una instalación
  suya. Un `terminal64.exe` de otra sesión de Windows no deja leer su `Path`, y
  ése es justamente el que no hay que tocar: antes el `Stop-Process` fallaba con
  acceso denegado y `-ErrorAction SilentlyContinue` lo tapaba.
- `Remove-Tree` dice ahora cuándo **no** pudo borrar. El log antiguo escribía
  «bases eliminado» sobre un borrado que había fallado entero, y la única etapa
  que decía la verdad era la verificación.
- La ruta de `mt5_path` se normaliza con `Path()` antes de pasarla: RoboForex
  declara su terminal principal con barras normales y el `Path` de un proceso de
  Windows viene con las invertidas.
- Lo vigila `tests/node/test_node_runtime_fork_parity.py`
  (`test_the_cleanup_of_every_fork_stops_at_its_own_terminals`), que además lee
  los dos `.ps1` de cada checkout: el criterio vive mitad en Python y mitad en
  PowerShell, y sólo esa guarda los mantiene juntos.
- Los botones manuales `Reparar` y `Prueba regresiva` envían siempre
  `cleanup_after_run: true`. El nodo intercala las tres etapas de limpieza
  después de cada run seleccionado, antes de comenzar el siguiente run del lote.
