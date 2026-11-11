# Dónde va cada cosa, y qué no puede salir de la raíz

Escrito el 2026-11-10, al cerrar la raíz con una lista blanca
(`tests/repository/test_repository_layout.py`). La regla vive en `AGENTS.md`;
aquí está lo que esa tabla no puede explicar: **por qué** mover algunos
ficheros de la raíz rompe producción sin que ninguna prueba lo diga, y en qué
estado quedó el reparto por dominios.

## Esto es una aplicación, no una librería: nada de `src/`

La guía de empaquetado de Python contrapone *flat layout* y *src layout*, y la
diferencia que decide aquí es que **el layout `src` obliga a instalar el
proyecto para poder ejecutarlo**. El manager se arranca sobre el árbol tal
cual —`python -m mt5_manager.manager --config manager.json`, `run_manager.bat`,
el `CMD` de la imagen—, y las pruebas importan `mt5_manager` desde el checkout.
Con `src/` por delante, todo eso necesitaría un `pip install -e .` previo que
hoy nadie hace. El paquete se queda en la raíz; lo que se agrupa es su
contenido.

## Los cuatro ficheros que parecen movibles y no lo son

Un «deploy/» con el Docker dentro es lo primero que se le ocurre a cualquiera.
No sale gratis:

- **`docker-compose.yml` y `docker-compose.dev.yml`.** Dos motivos
  independientes. Compose resuelve los *bind mounts* relativos contra la
  carpeta del fichero, así que `source: ./Caddyfile` y los montajes del
  proyecto apuntarían a `deploy/...` en cuanto el fichero baje un nivel; y
  `mt5_manager/manager/manager_restart.py` los busca por nombre en `repo_dir` para
  reiniciarse a sí mismo (`("docker-compose.yml", "compose.yml",
  "compose.yaml")`), de modo que el botón de reinicio dejaría de encontrar el
  despliegue. Lo segundo lo cubren `tests/manager/test_manager_restart.py`; lo
  primero no lo cubre nada.
- **`.dockerignore`.** Docker sólo lo aplica si está en la raíz del contexto de
  construcción. En cualquier otro sitio es un fichero de texto decorativo: la
  imagen seguiría construyendo, metiendo dentro `.git`, `runtime/` y `tests/`.
  Fallo mudo y caro.
- **`Caddyfile`.** Lo monta `docker-compose.yml` como `./Caddyfile`. Puede
  moverse, pero sólo junto con el compose, y el compose no se mueve.
- **`.env`.** Compose lo lee de la carpeta del proyecto y de ninguna otra.

## Los lanzadores llevan su propia trampa

`run_manager.bat` y `run_node.bat` empiezan con `cd /d "%~dp0"` y luego buscan
`manager.json` o `node.json` **en el directorio al que acaban de entrar**. Al
bajarlos a `scripts/` hay que subir uno: `cd /d "%~dp0.."`. Sin eso el `.bat`
no falla con un error de ruta, sino con su propio mensaje —«copia
config\manager.example.json como manager.json»— sobre un `manager.json` que sí
existe, un nivel más arriba. Tres minutos de desconcierto garantizados.

Si había un acceso directo en el escritorio a `run_node.bat` en la raíz,
dejó de funcionar el 2026-11-10: ahora es `scripts\run_node.bat`.

## `ubs/` no se toca de nombre

Son 69 líneas en tres ficheros y parece un candidato obvio a entrar en
`portfolio_manager/`. No lo es: **el nombre es el contrato**. Es el espejo del
paquete `ubs` del proyecto del agente, del que el manager importa
`ubs.path_utils` y `ubs.universe` con la misma ruta con la que los importa el
agente, y cuyo `ubs.mt5_symbol_extract` aparece en
`tests/node/test_symbol_sync.py`. Renombrarlo rompe el espejo en silencio.

## `assets/` no son recursos de la interfaz

Se llama así, pero dentro hay `<broker>_assets.ini`: universo y grupos de cada
broker, que lee `mt5_manager/experiment/experiment_service.py` y que la imagen copia
explícitamente (`COPY assets ./assets` en el `Dockerfile`). Es **dato de
producción**, no estáticos. Los estáticos de la interfaz están en
`mt5_manager/static/`.

## Estado del reparto por dominios

La raíz y `tests/` quedaron cerradas ese día:

- Raíz: lista blanca, un intruso falla con el nombre de la carpeta que le toca.
- `tests/`: ya no admite `test_*.py` suelto. Los treinta módulos que quedaban
  en la raíz de `tests/` bajaron a `live_audit/`, `manager/` y `portfolio/`.
  Lo que sigue arriba son piezas compartidas (`helpers.py`) y los dos
  baselines de tamaño.

**`mt5_manager/` se repartió el 2026-11-11**, en diez unidades con su commit
cada una, de la más aislada a la más enredada: `web_push`, `prop`,
`experiment`, `manual_check`, `monthly_summary`, `live_account`, `node`,
`manager`, `live_audit`, `portfolio`. El movimiento tocó unos 1.000 puntos (548
imports relativos dentro del paquete, 443 referencias `mt5_manager.<módulo>` en
código y pruebas, y un centenar fuera de Python), así que se hizo con un
reescritor idempotente que deduce dónde vive cada módulo del sistema de
ficheros y no de una lista. La forma final y sus cinco reglas están en
`AGENTS.md`; aquí quedan las trampas, que son lo que no se ve venir.

### Las cinco trampas de mover un módulo

Ninguna la delata el import, y tres de ellas dejan una guarda en verde.

1. **`Path(__file__)` cambia de profundidad.** Cinco módulos calculaban ahí su
   `STATIC_DIR` (`manager_handler`, `correlation_routes`, `experiment_routes`,
   `prop_experiment_routes`) o la raíz del repositorio (`manager_server`, para
   los `docker-compose*.yml` que busca `manager_restart`; y
   `portfolio_source_connection`, para `runtime/portfolio_snapshots`). Desde un
   dominio sobra un nivel. Lo demás seguía importando igual.
2. **Las guardas que localizan un módulo construyendo su ruta.** Catorce
   sitios hacían `ROOT / "mt5_manager" / f"{name}.py"`. Donde leían el fichero
   salió `FileNotFoundError`; donde alimentaban un `glob` —el
   `manager_node_source()` de la paridad del nodo y el
   `_manager_auditor_source()` del auditor— devolvieron **cero ficheros**, y la
   comparación siguió corriendo sobre la cadena vacía. Por eso existe
   `tools/package_layout.py`: `module_path`, `module_source` y `domain_sources`
   buscan por nombre en el paquete entero y fallan diciendo cuál no está.
3. **Las guardas que citan el import, no la ruta.**
   `test_live_screens_share_primitives` exigía literalmente
   `"from .live_account_performance import"` a las dos pantallas que miden una
   cuenta. El prefijo relativo depende de dónde viva el fichero —`.` para la
   vecina del dominio, `..live_account.` para el resumen mensual—, así que el
   criterio pasó a un `assertRegex` que no lo mira. Es la misma clase de fallo
   que «renombrar al mover rompe una guarda de texto», con el prefijo en lugar
   del nombre.
4. **El hook también lleva rutas.** `tools/hook_node_fork_warning.py` exigía
   `mt5_manager/node.py` pegado a la raíz, así que el aviso de «esto no lo
   ejecuta el agente» habría dejado de salir sin que nadie lo notara.
5. **Repartir un import con paréntesis entre dos dominios alarga el fichero.**
   `live_account_ingest.py` pasó de 600 a 605 líneas porque el bloque se partió
   a uno por línea. El techo de fichero no tiene baseline, así que los nombres
   se empaquetan como estaban.

### Lo que no se movió, y por qué

- **`guided_batches.py` y `guided_controller.py`** se quedan en la raíz del
  paquete. Son idénticos byte a byte al runtime de IC
  (`ai_context/ficheros_identicos_al_runtime_de_ic.md`) y `guided_controller`
  contiene `from .node import read_settings, memory_path`: desde un `guided/`
  ese import apuntaría a `mt5_manager.guided.node`. Por eso
  `node/__init__.py` reexporta esos dos nombres, y es el único `__init__` del
  paquete con contenido.
- **El nombre de cada módulo.** `live_audit_settings.py` sigue llamándose así
  dentro de `live_audit/`. Quitar el prefijo habría roto a la vez las guardas
  que nombran ficheros y los `patch()` de las pruebas sin añadir información.
- **Los comandos.** `python -m mt5_manager.manager` y `python -m mt5_manager.node`
  los sirven dos `__main__.py` de dos líneas. El import sí cambió:
  `from mt5_manager.manager.manager import ManagerServer`.
