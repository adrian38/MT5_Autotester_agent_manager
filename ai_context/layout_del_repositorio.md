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
  `mt5_manager/manager_restart.py` los busca por nombre en `repo_dir` para
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
broker, que lee `mt5_manager/experiment_service.py` y que la imagen copia
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

**`mt5_manager/` sigue plano**: 167 módulos agrupados sólo por el prefijo del
nombre (`live_audit_*`, `portfolio_*`, `manager_*`, `node_*`, ...). El objetivo
declarado es un subpaquete por dominio, y mientras no esté, la guarda exige al
menos que todo módulo pertenezca a un dominio declarado: un nombre nuevo que no
encaje en ningún prefijo falla. Lo que falta para cerrarlo —y por qué no se
hizo de una pasada— es que el movimiento toca unos 1.000 puntos (548 imports
relativos dentro del paquete, 443 referencias `mt5_manager.<módulo>` en código
y pruebas, y un centenar fuera de Python), y hay dos contratos que no perdonan
un movimiento a ciegas: los ficheros byte a byte con el runtime de IC
(`ai_context/ficheros_identicos_al_runtime_de_ic.md`) y la paridad del nodo,
cuyo `manager_node_source()` concatena todos los `node*.py` de
`mt5_manager/`. Una guarda que se debilita en silencio es peor que una que se
rompe, así que ese reparto va por unidades, con el suite completo detrás, y eso
lo pide el usuario de forma explícita.
