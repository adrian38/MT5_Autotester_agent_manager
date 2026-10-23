# Un `stat` cuesta 65 veces más desde el contenedor: 2026-10-23

El manager corre en Docker y ve el proyecto de cada agente por un bind mount
(`/data/ic`, `/data/axi`, `/data/roboforex`). Ahí **cada pregunta por un
fichero cuesta 3,4 ms**, contra 0,053 ms ejecutando el mismo código en el host.
Medido con 710 ficheros reales del árbol de AXI:

| desde | ms por `is_file()` |
| --- | --- |
| host, sobre `F:` directo | 0,053 |
| contenedor, sobre `/data/axi` | 3,413 |
| contenedor, sobre `/data/roboforex` | 3,033 |

Listar un directorio **no** es caro: `iterdir()` de siete carpetas con 1.896
entradas tardó 29 ms. Lo caro es preguntar por cada entrada: las mismas siete
carpetas con un `is_file()` por fichero tardaron 5.356 ms. Es la diferencia
entre un `readdir` y 1.896 `stat`.

## La regla

**Filtrar por nombre sobre una sola lectura del directorio; preguntar por el
fichero sólo de los que ya encajan.** Un predicado tan inocente como

```python
if path.is_file() and path.name.casefold() in expected   # mal
if path.name.casefold() in expected and path.is_file()   # bien
```

es la diferencia entre 7.584 preguntas y 6. El orden importa porque el `and`
cortocircuita, y comparar una cadena es gratis.

## Lo que pasó

«Portafolios activos» pide un `GET /api/nodes/<id>/live-dashboard` por nodo
cada 60 segundos. La tarjeta de AXI tardaba **24 s**; IC y RoboForex, 0,04 y
0,07 s. No eran los agentes —responden en 30-56 ms, también desde dentro del
contenedor— ni la red: el tiempo era del manager.

El gasto estaba en `live_audit_manual_inherit._legacy_reports`, a la que se
llega desde `dashboard_node_state` → `analysed_live_audit` →
`live_audit_executions.analysed_node_state` → `reapply`. Busca dos nombres
(`manual_<set>.htm` y `.html`) entre las ejecuciones guardadas del uso, y
preguntaba por **todas** las entradas de cada carpeta `reports/`, que guarda
además un artefacto automático por estrategia y ejecución. El uso
`audit-109-mvaqt8xd-1` tiene 4 estrategias pendientes y 7 ejecuciones con 2.148
ficheros: 7.584 preguntas por refresco.

Con el orden arreglado, medido dentro del contenedor sobre el mismo árbol y los
mismos 4 artefactos: **22,230 s → 0,201 s**, y las mismas 6 rutas encontradas.

## Por qué sólo se veía en AXI

El barrido sólo se ejecuta para las estrategias cuyo backtest no acredita el
100 % por sí mismo (`pending_artifacts`). IC no publicaba usos en real y los de
RoboForex no tenían ninguna pendiente: cero preguntas medidas. El síntoma era
de una tarjeta y arrastraba el refresco entero de la pantalla.

Y crecía solo: el coste es artefactos × ejecuciones × ficheros, así que cada
pasada del cron sobre ese uso lo empeoraba. `audit-105` ya acumula 13
ejecuciones.

## Cómo se diagnostica la próxima vez

1. Cronometrar el endpoint por nodo, no la pantalla:
   `curl -s -o /dev/null -w "%{time_total}" http://127.0.0.1:8750/api/nodes/<id>/live-dashboard`.
2. Descartar al agente pidiéndole lo mismo directamente
   (`/api/v1/live-audits` con su token), y repetirlo **desde dentro del
   contenedor**: ahí se separa la red de la CPU del manager.
3. Reproducir la ruta en el host con los almacenes reales y contar las llamadas
   a `Path.is_file`, parcheando el método para anotar quién pregunta
   (`sys._getframe(1)`). `cProfile` dice cuántas son; el parche dice de qué
   línea salen, que es lo que hace falta.
4. Un perfil del host **no** mide esto: ahí el barrido costaba 0,7 s y parecía
   razonable. El número que importa se mide en el contenedor.

## Lo que sigue abierto

El barrido heredado se repite en cada refresco aunque no encuentre nada: el
tope de reintentos (`_claim`) se consulta **después**, y cuando no hay nada que
enviar `reapply` sale antes de llegar a él, así que no queda memoria de haberlo
intentado. Ya no importa porque ahora cuesta 0,2 s, pero sigue siendo un
barrido de disco en una ruta de lectura que el navegador pide cada minuto: su
sitio natural es el vigilante del cron o un paso de arranque, no la portada.
Su propio docstring lo dice —«cargas aceptadas antes de que el manager
conservara su propia copia»—, es decir una migración de una vez.

Contrato y piezas de la herencia: `live_audit_manual_report_inheritance.md`.
