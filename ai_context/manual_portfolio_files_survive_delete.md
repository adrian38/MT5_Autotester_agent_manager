# Borrar un portafolio manual dejaba sus ficheros en el disco del agente

## El hallazgo (2026-10-18)

`POST /api/nodes/<node>/portfolio-manager/delete` —el botón «Borrar» del
inventario manual, commit `37f9947`, y el de Portafolio UBS desde antes— acaba
en `delete_portfolio` del nodo, que borraba filas de cinco tablas SQLite
(`portfolios`, `portfolio_members`, `portfolio_allocations`,
`portfolio_versions`, `portfolio_decision_log`) y nada más.

Un portafolio UBS no tiene nada más: sus `.set` son candidatos del pool, que
siguen siendo del pool. Uno manual sí: el runtime del agente copia los `.set`
que subió el usuario a `<runtime>/<nodo>/manual_portfolios/<uuid>/sets/`
(`manager_node_runtime/manual_portfolio.py::_write_sets`) y guarda la ruta
completa en `set_path`. Al borrar el portafolio, esa carpeta quedaba huérfana
**para siempre**: el único sitio que la nombraba era la fila que acababa de
desaparecer, y no hay recolector que recorra `manual_portfolios/`.

El síntoma es que no hay síntoma. El borrado responde `deleted` igual, la
tarjeta desaparece y el inventario queda correcto. Sólo se ve mirando el disco
del agente: el 2026-10-18 RoboForex tenía cuatro carpetas `<uuid>` y AXI una.
Las que ya estaban antes de este cambio siguen ahí; esto no las recoge.

## Dónde vive la regla

Como siempre con el nodo, la copia que se ejecuta es la del agente.

| Copia | Fichero |
| --- | --- |
| Agente (la que se ejecuta) | `manager_node_runtime/manual_portfolio.py` + `node_jobs_api.py::delete_portfolio` |
| Manager | `mt5_manager/portfolio_manual_files.py` + `PortfolioSource.delete_portfolio` |

El lado del manager **no es decorativo**, aunque los nodos de broker se
alcancen por HTTP: `PortfolioCoordinator._delete_on_node` escribe la memoria
directamente con `PortfolioSource` cuando la URL del nodo no es `http(s)` —un
montaje por letra de unidad o un bind mount— y también para el ámbito `grid`.
Ahí el manager sí ve el disco del agente y tiene que limpiarlo él.

Las dos copias llevan los mismos tres nombres a propósito
(`manual_portfolio_folder`, `manual_portfolio_folders_only_for`,
`discard_manual_portfolio_folders`): es el hilo que permite encontrar una desde
la otra, porque aquí no hay texto de error que buscar.

## La regla, y por qué es tan estrecha

Borrar ficheros no se deshace, así que `manual_portfolio_folder` devuelve una
carpeta sólo si se cumple todo:

1. el `set_path` guardado es absoluto y su padre se llama `sets`;
2. el abuelo —la carpeta que se va a borrar— cuelga de una llamada
   `manual_portfolios`;
3. esa carpeta cae dentro de la raíz de runtime que pasa el llamante
   (`self.runtime_dir` en el agente, `<proyecto>/runtime` en el manager);
4. ninguna fila de **otro** portafolio sigue apuntando a esa carpeta.

El `set_path` de un portafolio UBS es la ruta del candidato del pool
(`<proyecto>/data/<broker>/…`) y falla las tres primeras, así que nunca se
toca. La comparación con la raíz es por texto en minúsculas, no con `resolve()`:
resolver una unidad de red o mapeada que no responde cuesta segundos y esto
corre en cada borrado.

Dos cosas que **no** hace, a propósito:

- **No borra los informes OHLC.** `run_tests.py` los deja en
  `<proyecto>/reports/<nombre-del-set>.htm`, el mismo directorio que usan todos
  los backtests del agente, y `manual_history_summary` los relaciona por nombre.
  Borrar ahí por nombre de set podría llevarse el informe de un candidato
  homónimo del pool. Quedan, y los recoge la limpieza general de `reports/`.
- **No falla si la carpeta ya no está.** El portafolio ya no existe; repetir la
  acción no lo devuelve. `discard_manual_portfolio_folders` usa
  `shutil.rmtree(..., ignore_errors=True)` y devuelve sólo lo que de verdad
  desapareció (`removed_folders` en la respuesta del nodo).

En el manager, la lista de carpetas se calcula **antes** de borrar ninguna fila.
Son dos motivos en uno: la consulta de «las de los demás» no se confunde con las
que están desapareciendo, y `dev_branch.assert_writable` —obligatorio en todo
punto de escritura hacia el proyecto de un agente— rechaza el borrado entero en
vez de dejarlo a medias.

## Estado del port

| Copia | Borra la carpeta al borrar el portafolio |
| --- | --- |
| Manager (`portfolio_manual_files.py`) | sí, 2026-10-18 |
| ICTrading (`...MT5_Autotester_agent_IC\MT5_Autotester_agent`, rama `IC`) | sí, 2026-10-18 |
| AXI (`F:\TRADING\MT5_Autotester_agent_AXI`, rama `AXI`) | sí, 2026-10-18 |
| RoboForex (`G:\TRADING\MT5_Autotester_agent`, rama `dev`) | sí, 2026-10-18 |
| FUTURE (`...MT5_Autotester_agent_FUTURE`, rama `future`) | no aplica |

FUTURE es de una generación anterior: no tiene `manual_portfolio.py` ni las tres
rutas de «Solo portafolio», así que no puede crear ninguna de estas carpetas. El
día que converja con las otras ramas se llevará esto en el mismo merge. Por eso
tampoco está en `FORK_CANDIDATES` de `tests/node_runtime_parity_base.py`.

Las tres copias portadas necesitan **reiniciar la aplicación del agente**: el
nodo va embebido en `app_ui.py` y no recarga el módulo solo.

`test_deleting_a_manual_portfolio_frees_its_disk_on_every_reachable_fork`
(`tests/test_node_runtime_fork_parity.py`) exige en toda copia montada las dos
funciones, la llamada desde `delete_portfolio` —portarlas sin llamarlas no
cambia nada— y la prueba propia del agente.
