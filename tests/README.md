# Organización y ejecución de las pruebas

Durante la iteración se ejecuta el caso, la clase o el módulo mínimo que cubre
el cambio. La suite completa no forma parte automática del cierre: sólo se
ejecuta cuando el usuario la pide explícitamente.

## Estructura

- `repository/`: contratos del repositorio (capas, tamaños, layout,
  documentación y herramientas de verificación).
- `portfolio/`: comportamientos del dominio de portafolios, incluidos los
  informes de MT5 que los alimentan.
- `integration/`: integración HTTP local entre manager y nodo, separada en API
  general, portafolios y flujos del nodo.
- `manager/`: ciclo de vida, estado, pantallas e infraestructura del proceso
  manager, y sus primitivas compartidas.
- `node/`: ejecución y protocolo del nodo, incluida pausa/reanudación.
- `live_audit/`: auditoría y seguimiento de cuentas reales, con sus pantallas y
  el resumen mensual.

**La migración terminó el 2026-11-10.** La raíz de `tests/` sólo guarda piezas
compartidas (`helpers.py`) y los dos baselines de tamaño: un `test_*.py` suelto
lo rechaza `tests/repository/test_repository_layout.py`, que es la misma guarda
que cierra la raíz del repositorio.

Cada carpeta descubierta por `unittest` lleva `__init__.py`. Un traslado agrupa
un comportamiento completo, actualiza sus imports y comandos, y conserva el
recuento de casos; no se mezcla con cambios funcionales. Al bajar un módulo un
nivel hay que subir su `Path(__file__).parents[N]`: los veintinueve del
2026-11-10 pasaron de `parents[1]` a `parents[2]`.

## Escalera de verificación

1. Caso o clase afectados, por ejemplo
   `python -m unittest tests.repository.test_dev_branch.DevBranchTests`.
2. Módulos del comportamiento tocado, sin `-v` salvo que haya que diagnosticar.
3. Guardas y contratos con `python -m tools.verify_project` si el alcance lo
   justifica; este comando no ejecuta el suite completo.
4. Sólo por petición explícita del usuario, contrato completo con
   `python -m tools.verify_project --full`.

Las guardas del repositorio pueden ejecutarse directamente, por ejemplo:

`python -m unittest tests.repository.test_module_layering`
