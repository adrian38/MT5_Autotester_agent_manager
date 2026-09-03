# Sincronización de símbolos desde las tarjetas

## Estado (2026-09-03)

Implementadas la interfaz, el proxy HTTP del manager y la conexión real en la
copia ICTrading local, autorizada por el usuario. El nodo usa
`manager_node_runtime/universe_service.py` y `UniverseControllerMixin` desde
`manager_node_runtime/node.py`. AXI y RoboForex no se han modificado: necesitan
recibir el port antes de habilitar su botón. Reiniciar el manager y la app del
agente para cargar los cambios; no se han reiniciado procesos de producción ni
lanzado backtests reales durante la validación.

Cada tarjeta conectada muestra «Sincronización de símbolos». Solo se habilita
cuando el nodo anuncia `capabilities.universe_sync`. El diálogo contiene los
cinco pasos separados: sincronizar, probar history GEN, deshabilitar no_history,
deshabilitar `trade_disabled` y actualizar. No inicia automáticamente el probe
después de sincronizar.

La sincronización guarda además una captura derivada de `symbol_info.trade_mode`
para todos los símbolos devueltos por la terminal. El preview de trading bloqueado
usa esa captura MT5 (`DISABLED=0` y `CLOSEONLY=3`) como fuente vigente. Los
veredictos explícitos de journal solo son fallback para símbolos ausentes de la
captura; un `FULL` actual corrige un bloqueo histórico. El preview devuelve los
totales de cada fuente y la fecha de captura. La contraseña nunca entra en ella.

## Lógica existente que debe reutilizar el agente

Leída en la copia ICTrading de este equipo:
`C:/Users/Adrian/Adrian/TRADING/MT5_Autotester_agent_IC/MT5_Autotester_agent`.

- `ui/ubs_universe_logic.py::_sync_mt5_universe_symbols`: extrae MT5,
  reescribe el universo sin conservar grupos antiguos y deshabilita los retirados
  en GEN, eliminando sus excepciones de seeds. Hay backups de universo/política.
- `ubs/mt5_symbol_extract.py`: extracción y escritura existentes; no duplicarlas.
- `_ubs_history_probe_args` / `_run_ubs_universe_history_probe`: ejecuta
  `ubs_agent.py --probe-universe-history`, H1, un año desde la fecha Desde,
  respetando terminales, experto, mapa de símbolos, sufijos y memoria del agente.
- `_count_ubs_history_probe_symbols`: GEN activo sin veredicto final previo.
- `_no_history_universe_symbols` / `_disable_no_history_universe_symbols`:
  último veredicto por símbolo de candidatos con `policy='history_probe'`.
- `_trade_disabled_universe_symbols` / `_disable_trade_disabled_universe_symbols`:
  último veredicto normal por símbolo; solo admite `trade_disabled`, que exige
  cero operaciones y evidencia explícita del journal (close-only/10044 o
  trade-disabled/10017).

Los métodos de UI dependen de Tk: no invocarlos desde el hilo HTTP. El nuevo
servicio reutiliza `ubs.mt5_symbol_extract`, `ubs.universe` y `ubs.account`, y
lanza el CLI existente con `--probe-universe-history`. Cambiar solo
`mt5_manager/node.py` no conecta nada en el agente embebido. La copia de referencia
del manager no anuncia esta capacidad: la implementación pertenece al agente.

## Contrato preparado en el manager

Todas las rutas del manager son POST `/api/nodes/<id>/<acción>`:

| Acción | POST del nodo | Resultado esperado |
| --- | --- | --- |
| `universe-sync` | `/api/v1/universe/sync` | `total`, `added`, `removed`, `newly_disabled` numéricos |
| `universe-history-preview` | `/api/v1/universe/history-preview` | `pending`, `from_date`, `to_date` |
| `universe-history` | `/api/v1/jobs/universe-history` | aceptación del job, sin esperar los backtests |
| `universe-disable-preview` | `/api/v1/universe/disable-preview` | `total`, `already_disabled`, `newly_disabled`, `symbols` (solo los nuevos) |
| `universe-disable-no-history` | `/api/v1/universe/disable-no-history` | `newly_disabled` |
| `universe-trade-disabled-preview` | `/api/v1/universe/trade-disabled-preview` | `total`, `already_disabled`, `newly_disabled`, `symbols`, `terminal_total`, `journal_total`, `journal_fallback_total`, `terminal_captured_at` |
| `universe-disable-trade-disabled` | `/api/v1/universe/disable-trade-disabled` | `newly_disabled` |

Sincronizar envía `mt5_path`, `login` (texto numérico o vacío), `server`,
`password`. Vacíos significan sesión/terminal configurados. El nodo debe validar
los campos, no persistir ni registrar la contraseña y no incluirla en el estado
del job. El manager no guarda ninguna credencial en preferencias.

Deshabilitar envía exactamente `{symbols: [...]}` del preview confirmado. El
nodo debe intersectar ese conjunto con los veredictos vigentes y nunca ampliar
la selección a símbolos nuevos no confirmados. Las mutaciones deben rechazar
agente ocupado, auditoría activa o pipeline pendiente de reanudación, respetar
los candados de escritura y no pisar el estado de una ejecución existente.

El job histórico se integra en el control de procesos/logs del nodo y expone
`current_stage='universe_history'`. `_launch_next_runnable` conoce esa etapa para
reanudarla sin convertirla en generación. El lanzamiento es rápido; la
preparación silenciosa pertenece al proceso. Las fechas del año H1 se guardan
en el request para reanudaciones. Un callback que solo lee el handle del proceso
de UI impide iniciar el flujo sobre una ejecución local. La UI también bloquea
`_run_script` si el nodo tiene un proceso o una sincronización activos.

El nodo comprueba que memoria y output del probe, universo y política estén en
su propio proyecto con su `assert_writable`, además del candado `dev` del manager.
Un universo vacío o una política JSON dañada no se sobrescriben. Los errores de
extracción eliminan la contraseña antes de devolverse y no persisten el payload.

El proxy mantiene los errores HTTP del nodo, amplía el timeout a 120 s para
estas acciones y no reintenta mutaciones tras un timeout. En `dev` valida
`portfolio_project_dir` con `assert_writable` antes de contactar el nodo; si
falta la ruta, rechaza la petición.

## Verificación

- `python -m unittest tests.test_symbol_sync tests.test_static_portfolios.NodeCardControlsTests -v`: 10 OK.
- `python -m unittest discover -s tests -v`: 412 OK.
- `node --check mt5_manager/static/app.js`: OK.
- Navegador con servidor simulado: tarjetas con/sin capacidad y apertura del
  diálogo verificadas. El control del navegador agotó el tiempo al accionar la
  confirmación nativa; no se verificó la secuencia completa por navegador.
- `tests.test_symbol_sync` incluye una prueba de extremo a extremo que arranca
  el runtime real de ICTrading en otro intérprete, reenvía desde ManagerServer,
  sincroniza archivos temporales y ejecuta un CLI simulado hasta completar el
  job/log. Se omite explícitamente si esa copia no está montada.
- Suite del agente: 594 OK, incluidos 10 tests nuevos de universo remoto.
- Construcción del comando con `manager_node.json` real de ICTrading verificada:
  proyecto propio, probe H1 y multiterminal. Solo inspección; no se ejecutó.
- El índice del manager se actualizó. El MCP permite consultar el índice previo
  del agente, pero rechaza reindexarlo porque está fuera de su raíz permitida.
  No se alteró esa restricción; para los cambios del agente se contrastó el grafo
  previo con lectura directa y pruebas de su runtime.
