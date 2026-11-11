# Contrato único de verificación

`python -m tools.verify_project` sincroniza las reexportaciones UBS y ejecuta
las auditorías de nombres, tamaños e índice, las guardas arquitectónicas y, si
la copia IC está montada, sus pruebas focales del pipeline guiado. El suite
completo del manager sólo se añade con `--full` cuando el usuario lo haya pedido
explícitamente; `--quick` se conserva como alias del comportamiento normal.

## Iteración focal y disposición de las pruebas

Durante el trabajo se ejecuta el caso, la clase o los módulos del comportamiento
tocado. `python -m tools.verify_project` conserva las auditorías, las guardas y
el contrato focal de IC sin ejecutar el suite del manager. El cierre, el commit
y la entrega no autorizan el suite completo: `--full` sólo se usa tras una
petición explícita del usuario.

Las guardas del repositorio viven en `tests/repository/`. Es el primer grupo
migrado desde la raíz plana; el resto se moverá incrementalmente por dueño y
comportamiento, conservando el recuento de pruebas y sin mezclar el traslado con
cambios funcionales. `tests/README.md` mantiene la taxonomía y los comandos.

El comando captura `HEAD` y el estado tracked del manager y de IC antes de las
pruebas. Si otra sesión cambia alguno durante la medición, el resultado falla:
una prueba verde ya no describe necesariamente el código que se va a entregar.
Los ficheros sin seguimiento no se comparan para que el propio trabajo nuevo no
invalide la medición; aun así deben revisarse en el diff final.

## Dónde busca la copia de IC

`verify_project` derivaba la ruta de IC de donde estuviera el manager
(`ROOT.parent / "MT5_Autotester_agent_IC" / "MT5_Autotester_agent"`). En este
equipo el manager cuelga de `I:\TRADING`, así que buscaba IC en
`I:\TRADING\MT5_Autotester_agent_IC\...`, que no existe: **el contrato focal de
IC se omitía siempre**, y el único aviso era una línea `[OMITIDO]` enterrada al
final de una salida de cien segundos. Es el mismo desfase de rutas que
`CLAUDE.md` documenta sobre `mt5_manager/dev_branch.py` y las notas viejas de
`ai_context/`, escritas cuando el manager vivía en
`C:\Users\Adrian\Adrian\TRADING`.

Ahora la ruta sale de `tools/agent_projects.py`, una **lista de candidatas** que
se recorre en orden, como ya hacía `FORK_CANDIDATES`. `ic_project(*required)`
devuelve la primera copia que contiene todo lo que pide quien llama; se exigen
los cuatro módulos del contrato, no la carpeta, porque una copia a medio montar
daría por ejecutado un contrato que no está ahí. `FORK_CANDIDATES` reutiliza esa
misma lista para sus rutas de IC, y la guarda byte a byte de
`test_guided_routing` —que se saltaba sola por la misma causa— también.

### El módulo tampoco se escribe a mano

El contrato nombraba sus cuatro pruebas como módulos (`tests.test_guided_node`),
y eso volvió a omitirlo en silencio el 2026-11-11, cuando IC repartió su
`tests/` en subpaquetes: `test_guided_node.py` bajó a `tests/guided/` y
`test_manager_node_repair_phases.py` a `tests/manager_node/test_repair_phases.py`.
La lista de candidatas seguía encontrando la carpeta, pero `ic_project` exigía
las rutas planas, así que ninguna copia «tenía» el contrato. Y no vale cambiar
la lista por la nueva forma: AXI, RoboForex y Vantage siguen planas.

Ahora el contrato se declara por **tema** (`IC_CONTRACT_TOPICS`:
`prepared_candidates`, `guided_node`, `guided_http`, `repair_phases`) y
`tools.agent_projects.agent_test_module` lo traduce a módulo en cada copia
buscando por el final del nombre, que es lo único que las dos formas comparten.
Sigue siendo todos o ninguno.

Lo mismo le pasó a las guardas de paridad del nodo, que buscaban las pruebas
del fork con `glob("test_manager_node_*.py")` sobre un solo nivel: doce se
pusieron rojas y cinco reventaron con `FileNotFoundError` al leer
`tests/test_manager_node_lifecycle.py`. Ahí la puerta es
`fork_node_tests(project, topic)` de `tests/node/node_runtime_parity_base.py`,
que reconoce una prueba del nodo por llevar `manager_node` en su ruta —en el
nombre del fichero o en la carpeta— y así cubre las dos formas.

La omisión no pone el resultado en rojo: hay equipos donde legítimamente no hay
IC montado. Pero deja de estar enterrada: el paso nombra el proyecto que usa
(`[VERIFICA] contrato IC en <ruta>`), la omisión dice dónde se buscó, y la
**última línea** pasa a ser `[OK] contrato verificado SIN: contrato IC` en vez
del `[OK] contrato completo` de siempre.

## Importaciones estrella

`tools.undefined_names` no puede demostrar qué nombres aporta un `import *`.
Por eso sólo admite las seis fachadas históricas de auditoría enumeradas en
`ALLOWED_STAR_IMPORTS`. `tests/repository/test_undefined_names.py` exige igualdad exacta:
una importación estrella nueva y una excepción que ya no se use fallan.

## Ramas, espejos y pruebas

**Ya no hay matriz de escritura por rama.** Describía el manager repartido
entre dos PC —una en `dev` y otra en `main`— y el usuario la retiró el
2026-10-16: todos los checkouts montados son escribibles, en cualquier rama.
La autorización amplía la ubicación y nunca el objetivo pedido.

`test_agents_contract_documents_the_enforced_workflow` exigía esa cabecera
aunque detrás no quedase ninguna guarda; desde el 2026-10-19 exige
`tests/node/test_node_runtime_fork_parity.py`, que sí falla sola. El candado de
`mt5_manager/dev_branch.py` sigue en el árbol e inerte fuera de `dev`: la rama
no se usa hoy, pero el raíl importa más con los tres agentes en el mismo
equipo que cuando estaban repartidos.

Los dos ficheros guiados de
producción son espejos byte a byte y requieren commits hermanos cuando cambian;
las pruebas específicas de manager o IC no son espejos y pueden corregirse en
el repositorio que contiene su contrato.

La entrega debe identificar commits, pruebas y recuentos, copias omitidas,
acciones de porting o reinicio y el estado de cada checkout. Si la memoria de
código no estuvo disponible, se declara la limitación y nunca se usa una
búsqueda negativa del grafo para afirmar que algo no existe.
