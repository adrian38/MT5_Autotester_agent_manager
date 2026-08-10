# El manager no puede escribir la memoria de un agente remoto

## Síntoma

«Cambiar estado» en las tablas de estrategias excluidas devolvía un toast con el
texto crudo de SQLite: **`disk I/O error`**. Ocurrió el 2026-08-10 con el nodo
`roboforex-ecn-192-168-1-152` (`X:\TRADING\MT5_Autotester_agent`, que es
`\\Desktop-e2vtfpq\G`). Excluir sí funcionaba en la misma pantalla, y eso es
justamente la pista: la exclusión se delega al nodo, el cambio de estado no.

## Causa

La memoria UBS está en **modo WAL** (`pragma journal_mode` = `wal` en todas). Para
leer o escribir una base WAL, SQLite necesita el índice compartido `-shm`, y hay
sistemas de ficheros que no pueden respaldarlo: recursos de red (CIFS/SMB, NFS) y
los bind mounts de Docker Desktop (9p, virtiofs, gRPC-FUSE). Ahí abrir la base
falla con `disk I/O error`.

El manager que sirve `:8750` **corre en Docker** (`docker-compose.yml` +
`runtime/manager.docker.json`): el proyecto de cada agente entra como bind mount
(`/data/ic`, `/data/axi`, `/data/roboforex`), así que la memoria de un agente nunca
es local para él, ni siquiera cuando el agente está en el mismo equipo.

La **lectura** ya lo trataba: `PortfolioSource._needs_snapshot_read` detecta esos
sistemas de ficheros y `connect_memory` lee una copia en un disco que sí soporta el
`-shm` (`_remote_read_snapshot`). La **escritura no tenía salida equivalente** —y no
puede tenerla: una copia no sirve, la escritura tiene que ir al original. Iba
directa a `sqlite3.connect(memory)` y solo traducía los errores con «locked» o
«busy»; `disk I/O error` se relanzaba tal cual, `manager.py` lo capturaba como
`sqlite3.Error` y lo devolvía como 400 con el mensaje de SQLite.

## Regla

**Toda escritura sobre la memoria de un agente tiene que ejecutarla el nodo del
agente, que la tiene en local.** No es una preferencia de diseño: es la única
forma de que la escritura llegue. Ya estaba escrito en `mt5_manager/node.py`
(«This MUST run on the node… writing to it directly over CIFS is unreliable
because SQLite's WAL is not coherent across a network share») y aplicado a la
exclusión individual, la múltiple y el borrado. `requalify` se quedó fuera porque
su documentación afirmaba que el endpoint del nodo «no aporta nada aquí»: aporta lo
único que importa, que la base sea local para quien escribe.

Criterio en código: `PortfolioSource.write_needs_node(memory)`, que es el mismo
predicado que decide copiar para leer. `PortfolioCoordinator.requalify` lo consulta
y delega en `_requalify_on_node`; con la memoria local sigue escribiendo el
manager, que es el único caso en el que esto funcionaba antes y no puede empezar a
depender de un endpoint portado a mano.

## Al añadir una escritura nueva sobre la memoria de un agente

1. Preguntar **qué proceso ejecuta la línea que escribe**. Si es el manager y la
   memoria puede venir por red o por bind mount, no funciona: hay que delegar.
2. Delegar con el patrón de `exclude` y `_delete_on_node`: `_post_to_node`, 404 →
   decir qué falta portar, y una clave de confirmación en la respuesta
   (`deleted`, `verdict_applied`, `requalified`) para que un nodo antiguo no pueda
   devolver 200 sin haber escrito nada.
3. Invalidar la copia de lectura después (`invalidate_after_exclusion` /
   `_invalidate_node_snapshots`), ver `manager_snapshot_after_node_writes.md`.
4. Portar a `manager_node_runtime/` de cada agente con un diff, duplicar la prueba
   en `tests/test_manager_node_*.py` del agente y **reiniciar su aplicación**.

## Lo que sigue sin poder escribirse desde el manager

- **Veredicto de una exclusión Grid.** La fila de cuarentena Grid vive en la base
  del manager (local, sin problema), pero el veredicto de etapa va a la memoria del
  broker (`_quarantine_grid_set` → `exclude_strategy` → `_apply_candidate_verdict`).
  Sobre un bind mount eso falla igual. No se puede delegar entero al nodo: el
  endpoint exige un `portfolio_id` de la memoria del broker y un paquete Grid solo
  existe en el manager. Hoy el error es explícito en vez de crudo: `connect_memory`
  traduce el fallo de escritura a un mensaje que dice que esa memoria solo la puede
  escribir el nodo. Excluir en Grid **con motivo manual** no se ve afectado.

## Detalle del entorno, para reproducir

- Manager en Docker: `docker-compose.yml`, bind mounts `IC_PROJECT_DIR`,
  `AXI_PROJECT_DIR`, `ROBOFOREX_PROJECT_DIR` → `/data/*`.
- La imagen copia el código sin `.git`, así que **el candado de la rama `dev` está
  inerte dentro del contenedor** (`dev_branch.is_active()` lee `.git/HEAD`). Con el
  código de `dev` corriendo fuera de Docker, escribir en la memoria de RoboForex lo
  rechaza `assert_writable` antes de llegar a SQLite. Al probar, tenerlo en cuenta:
  el mismo botón falla por dos motivos distintos según dónde corra el manager.

## Pruebas

- `tests/test_exclusion_verdict.py::RequalifyRoutingTests`: a quién se manda la
  escritura, qué dice un nodo sin portar, que un nodo que no confirma no se cree, y
  que la memoria local sigue escribiéndose desde el manager. La última fija la
  traducción del `disk I/O error` a un mensaje accionable.
- `tests/test_node_runtime_fork_parity.py::test_changing_the_state_of_an_excluded_strategy_reaches_every_reachable_fork`.
- Agente IC: `tests/test_manager_node_portfolio_save.py::ManagerNodeRequalifyTests`.
