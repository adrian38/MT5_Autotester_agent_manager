# Dos ficheros tienen que ser idénticos byte a byte al runtime de ICTrading

`mt5_manager/guided_batches.py` y `mt5_manager/guided_controller.py` se comparan
**byte a byte** contra `manager_node_runtime/` del proyecto de ICTrading en
`tests/test_guided_routing.py::test_portable_protocol_matches_actual_ic_runtime`
(normalizando sólo CRLF, porque la igualdad se rompía sola en el primer checkout
de cualquiera de los dos lados).

No es una copia por comodidad: es el protocolo de los lotes guiados. Si el
manager firma un lote con una versión y el agente lo verifica con otra, el lote
se rechaza sin decir por qué.

## Qué significa para un refactor

- **No se les puede añadir ni un import.** Al partir `node.py` moví
  `_normalize_generation` a `node_job_starts` y añadí el import correspondiente
  a `guided_controller.py`: la prueba falló al instante. La solución fue dejar
  `_normalize_generation`, `_enqueue`, `_schedule_queue_drain` y el resto de la
  cola como métodos de `JobController` que delegan al módulo.
- **`validate_package` (87 líneas) se queda por encima del techo.** Es la única
  función de producción que lo supera, y está en el baseline por esto. Partirla
  exige tocar la copia del agente, que sólo está autorizada en `dev`.
- Si algún día hay que partirla: cambiar las dos copias en el mismo commit, en
  `dev`, y comprobar que la prueba sigue verde antes de dar nada por hecho.

## El resto del nodo sí se puede partir

`mt5_manager/node.py` y sus diez módulos hermanos **no** tienen esa restricción:
la copia del agente está renombrada y diverge a propósito. Lo único que los une
es el texto del mensaje al usuario, y de eso se ocupa
`tests/test_node_runtime_fork_parity*.py` a través de `manager_node_source()`,
que concatena todos los `node*.py` para comparar criterios y no ficheros.
