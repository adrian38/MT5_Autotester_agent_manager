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
- **Partir sí se puede, si se parten las dos copias en el mismo commit.**
  `validate_package` tenía 87 líneas y era la única función de producción por
  encima del techo. Se partió en siete pasos con nombre el 2026-09-27,
  escribiendo el mismo texto en los dos ficheros —cada uno con sus finales de
  línea, CRLF aquí y LF en IC— con el script
  `scratchpad/split_validate_package.py`. Ya no queda ninguna excepción en
  `tests/function_length_baseline.json`.
- Requiere permiso explícito sobre la copia del agente: en `dev` lo da el
  alcance de la rama; fuera de `dev` lo tiene que dar el usuario para la tarea.
- **Un suite verde no basta para este fichero.** Es validación de protocolo: un
  `raise` que se cae deja pasar lotes manipulados sin que ninguna prueba lo
  note. Se comparó contra `git show HEAD:` con 620 lotes construidos a propósito
  —sobre, campos del candidato, modos, timeframes, los dos `.set` codificados,
  ocho manipulaciones del contenido en cada lado, mutación numérica, retargeting
  y recuperación de símbolo, duplicados— exigiendo el **mensaje exacto** del
  `ValueError` y no sólo que fallara. 24 de los 620 son lotes válidos, para que
  el camino positivo también entre: 0 divergencias.

## El resto del nodo sí se puede partir

`mt5_manager/node.py` y sus diez módulos hermanos **no** tienen esa restricción:
la copia del agente está renombrada y diverge a propósito. Lo único que los une
es el texto del mensaje al usuario, y de eso se ocupa
`tests/test_node_runtime_fork_parity*.py` a través de `manager_node_source()`,
que concatena todos los `node*.py` para comparar criterios y no ficheros.
