# Experimenta lee AXI y RoboForex desde dev sin abrir sus operaciones

## Problema

El manager `dev` corre en Docker en la cuenta de ICTrading. Sus rutas normales
`/data/axi` y `/data/roboforex` no contienen los proyectos de la otra cuenta, de
modo que el laboratorio «Experimenta» solo veía IC aunque los nodos HTTP de AXI
y RoboForex sí respondían.

## Decisión

`docker-compose.dev.yml` monta los shares `F` y `G` del otro equipo mediante
CIFS, con credenciales tomadas del `.env` ignorado por Git. Ambos mounts son de
solo lectura y viven bajo `/experiment-data/*`, separados de `/data/*`.

`ExperimentCoordinator.readonly_source` centraliza el consumo de esas rutas, mediante
`MT5_MANAGER_EXPERIMENT_AXI_PROJECT_DIR` y
`MT5_MANAGER_EXPERIMENT_ROBOFOREX_PROJECT_DIR`. Lo consumen únicamente dos
pantallas de análisis de solo lectura: Experimenta y Correlación. Las pantallas
operativas UBS, mensual y Grid conservan sus rutas anteriores y siguen sin poder
operar sobre AXI o RoboForex desde `dev`.

El reinicio automático añade `docker-compose.dev.yml` únicamente cuando el
checkout activo es `dev`. En `main` no carga el override y el comportamiento de
producción no cambia.

No se modificó ningún proyecto de agente ni su `manager_node_runtime/`: esta
función es exclusivamente una lectura del manager y no necesita porting.
