# Rutas de agente en la rama de pruebas

## Problema

`manager.json` y `node.json` están fuera de Git (`.gitignore`), pero la copia de
trabajo de este equipo describe tres nodos y dos de ellos viven en otra PC:

| Nodo | `portfolio_project_dir` | URL |
| --- | --- | --- |
| `ictrading-standard-test` | `C:\Users\Adrian\Adrian\TRADING\MT5_Autotester_agent_IC\MT5_Autotester_agent` | `127.0.0.1:8761` |
| `axi-standard-192-168-1-152` | `Y:\TRADING\MT5_Autotester_agent_AXI` | `192.168.1.152:8762` |
| `roboforex-ecn-192-168-1-152` | `X:\TRADING\MT5_Autotester_agent` | `192.168.1.152:8761` |

En la rama de pruebas `dev` solo existe el agente local de ICTrading (el usuario
`test` que ya se usaba antes). `X:` e `Y:` son unidades mapeadas al otro equipo,
así que cualquier prueba que las toque falla o lee memoria ajena.

## Decisión

`mt5_manager/dev_branch.py` corrige la configuración **solo cuando la rama activa
es `dev`**:

- `apply_manager_config` fuerza `portfolio_project_dir` al agente local en los
  nodos con `portfolio_broker` `ICTRADING` y descarta sus
  `portfolio_memory_path` / `portfolio_memory_paths` (apuntaban a la otra PC; el
  servicio los deriva del `project_dir` cuando faltan).
- La lista de nodos se conserva entera. Las tarjetas de AXI y RoboForex siguen en
  el panel con sus rutas de producción: no son el objeto de las pruebas y abrirlas
  falla igual que antes de este módulo. Filtrarlas escondía nodos configurados y
  no aportaba nada a las pruebas.
- `apply_node_config` fuerza `project_dir` del nodo al mismo agente.
- **Cuál es «el agente local» no se escribe a mano.** `DEV_PROJECT_CANDIDATES`
  lista los checkouts de IC conocidos y `first_mounted_project` elige el primero
  que tiene `manager_node_runtime/`. Ver más abajo.
- Los puntos de enganche son los dos `main()`: `mt5_manager/manager.py` y
  `mt5_manager/node.py`, justo después de `load_json`.

Fuera de `dev` ambas funciones devuelven **el mismo objeto**, sin copiarlo ni
tocarlo. El merge a `main` arrastra el módulo pero queda inerte: las rutas de
producción no se contaminan. La condición es la rama, no el fichero, por eso no
hace falta un `manager.json` distinto por entorno.

## El límite de escritura

Corregir la ruta no basta: las tarjetas de AXI y RoboForex siguen en el panel, y
una acción sobre ellas no debe poder escribir en la otra PC. `assert_writable`
rechaza con `ValueError` —el error que manager y nodo traducen a 400 con el
mensaje visible— cualquier destino fuera de `writable_roots()`:

| Raíz permitida | Por qué |
| --- | --- |
| el checkout de IC elegido (`DEV_PROJECT_DIR`) | única dirección de nodo escribible en pruebas |
| `runtime/` de este repositorio | estado propio del manager: preferencias, `portfolio_settings.json`, base Grid, copias de lectura |
| temporal del sistema | copias de lectura cuando `runtime/` está en un bind mount, y los proyectos ficticios de las pruebas |

Es una lista de permitidas, no de prohibidas: lo que no aparece se rechaza. Los
puntos de aplicación son los dos únicos por los que se escribe en el proyecto de
un agente:

- `PortfolioSource.connect_memory` con `write=True`. Es el embudo real: todas las
  escrituras en SQLite —guardar, cuarentena, deshacer, borrar, reoptimizar, Grid—
  pasan por ahí, tanto en el manager como en el nodo (`save_portfolio_payload`).
  Con `write=True` se conecta siempre a la base original, nunca a una copia de
  lectura, así que la comprobación es exacta.
- `PortfolioSource.export_portfolio`, que crea la carpeta de exportación y admite
  un destino elegido por el usuario.

### Dónde está el agente: lista, no constante

`DEV_PROJECT_DIR` era una ruta escrita a mano
(`C:\Users\Adrian\Adrian\TRADING\MT5_Autotester_agent_IC\MT5_Autotester_agent`),
la de una máquina donde el usuario era `Adrian`. En este equipo el agente cuelga
de `C:\Users\13199\...` y el manager de `I:\TRADING`, así que en `dev` el nodo
quedaba apuntado a una carpeta inexistente **y el candado rechazaba toda
escritura al agente real**: no es el fallo silencioso de `verify_project`, es la
rama de pruebas inservible.

Ahora `DEV_PROJECT_CANDIDATES` enumera los checkouts conocidos —el de este
equipo, el de la máquina `Adrian` y el vecino del manager, que es lo que se
asumía antes— y `first_mounted_project` devuelve el primero que tiene
`manager_node_runtime/`. Se exige esa carpeta y no sólo el nombre: una carpeta
con el nombre correcto pero sin la bifurcación no es el proyecto de un agente.
Si no hay ninguna montada devuelve la primera, que no existe: el candado es una
lista de permitidas, así que quedarse sin ninguna **cierra** la escritura.

Dos cosas que no cambian:

- **La elección se hace una vez, al importar.** El candado corre en cada
  escritura y tocar el disco ahí es justo lo que hay que evitar (ver el apartado
  siguiente).
- **El candado sigue teniendo una sola raíz de agente.** La lista sirve para
  encontrar el agente, no para ampliar lo permitido: las candidatas no elegidas
  se rechazan igual que `Y:` o `X:`, y hay prueba.

La lista vive en `mt5_manager/dev_branch.py` y `tools/agent_projects.py` la
reexporta como `IC_PROJECT_CANDIDATES`, no al revés: la imagen Docker copia
`mt5_manager`, `portfolio_manager`, `ubs` y `assets`, pero no `tools/`, así que
un import en ese sentido rompería el arranque del contenedor. De ahí cuelgan
también `FORK_CANDIDATES` y el contrato focal de IC de `verify_project`: una
sola lista para los tres.

### El candado no puede resolver el destino

`assert_writable` resuelve solo las raíces permitidas, jamás la ruta de destino.
Resolver `Y:\...` o `\\192.168.1.152\...` cuando no responden bloquea segundos
por llamada, y el candado corre en cada escritura: la primera versión hizo que
`tests/repository/test_dev_branch.py` pasara de 0,05 s a 27,7 s. Las raíces permitidas sí
existen, así que resolverlas es inmediato y cubre que lleguen por enlace o por
letra mapeada. El destino se compara en forma absoluta: una ruta permitida
expresada de forma exótica se rechazaría, lo que falla del lado seguro.

## Por qué se lee `.git/HEAD` y no `git rev-parse`

Evita depender de que `git` esté en el `PATH` y de pagar un subproceso en cada
arranque. Se resuelve el caso worktree/submódulo (`.git` como fichero con
`gitdir:`). Con `HEAD` desprendido o sin `.git` visible (paquete instalado,
imagen Docker) la redirección queda desactivada.

En Docker `.git` está en `.dockerignore`, así que `docker_entrypoint.py` no ve
rama y no redirige: allí las rutas ya las decide `.env` mediante los binds
`/data/ic`, `/data/axi`, `/data/roboforex`.

## Escapes por entorno

- `MT5_MANAGER_DEV_OVERRIDE=0` desactiva la redirección estando en `dev`
  (por ejemplo para reproducir el reparto de producción).
- `MT5_MANAGER_DEV_OVERRIDE=1` la activa desde cualquier rama.
- `MT5_MANAGER_DEV_PROJECT_DIR` cambia la ruta destino sin editar código, útil si
  el agente de pruebas se mueve de disco. Gana a `DEV_PROJECT_CANDIDATES`, y el
  candado lo sigue. **No sustituye a la lista**: hay que ponerla en el entorno de
  cada proceso (manager, nodo, `app_ui`) y, si se olvida, el síntoma no es un
  aviso sino que `dev` deja de poder escribir en el agente.

## Verificación

`tests/repository/test_dev_branch.py` cubre detección de rama (normal, worktree, `HEAD`
desprendido, sin repositorio), la corrección en `dev` comprobando que AXI y
RoboForex siguen en la lista con sus rutas originales, la identidad del objeto en
`main`, los dos escapes por entorno y el caso de `dev` sin nodo ICTrading (deja
la configuración como está y lo avisa por consola en lugar de inventar un nodo).

Del candado cubre: destino permitido, rechazo de `Y:`, `X:`, UNC y de otra copia
local del agente, el estado propio del manager y los temporales como escribibles,
el paso libre fuera de `dev`, y que el candado siga a
`MT5_MANAGER_DEV_PROJECT_DIR` cuando se redirige el agente.

De la elección del checkout cubre: que `DEV_PROJECT_DIR` sea la primera
candidata montada en el equipo que ejecuta las pruebas (y la primera a secas si
no hay ninguna), que una carpeta sin `manager_node_runtime/` no se acepte, y que
las candidatas no elegidas sigan siendo destinos rechazados.

La suite completa se ejecuta con el candado activo —está en `dev`— y sus
proyectos ficticios viven en el temporal, que es raíz permitida: `Ran 232 tests
... OK`. Si una prueba futura creara un proyecto fuera del temporal, fallaría al
escribir; el arreglo es mover la prueba al temporal, no ampliar
`writable_roots()`.
