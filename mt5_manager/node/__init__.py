"""El nodo de **este** repositorio. NO es el que corre en los brokers.

Cada agente ejecuta su copia bifurcada y renombrada en `manager_node_runtime/`,
asi que el aviso de «El nodo NO ejecuta este repositorio» de `AGENTS.md` vale
igual para los once modulos de aqui.

Es el unico `__init__` del paquete con contenido, y por una razon concreta:
`mt5_manager/guided_controller.py` tiene que ser **identico byte a byte** al del
runtime de IC y contiene `from .node import read_settings, memory_path`. Esa
linea no se puede tocar, se resuelve desde la raiz del paquete y ahora `node` es
este paquete, asi que los dos nombres entran aqui. Nada mas: un `__init__` que
reexportara el dominio entero acoplaria cada submodulo al resto.
"""
from .node_settings import memory_path, read_settings  # noqa: F401
