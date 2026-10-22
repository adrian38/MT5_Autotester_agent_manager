# Organización y ejecución de las pruebas

Durante la iteración se ejecuta el caso, la clase o el módulo mínimo que cubre
el cambio. La suite completa no forma parte automática del cierre: sólo se
ejecuta cuando el usuario la pide explícitamente.

## Estructura

- `repository/`: contratos del repositorio (capas, tamaños, documentación y
  herramientas de verificación).
- `portfolio/`: comportamientos del dominio de portafolios. La migración ha
  comenzado por creación, consulta y borrado de portafolios manuales.
- `integration/`: integración HTTP local entre manager y nodo, separada en API
  general, portafolios y flujos del nodo.
- `manager/`: ciclo de vida, estado e infraestructura del proceso manager.
- `node/`: ejecución y protocolo del nodo, incluida pausa/reanudación.
- La raíz conserva temporalmente los módulos todavía no migrados.
- Los siguientes grupos se moverán de forma incremental a `live_audit/`; los
  comportamientos restantes se incorporarán a `manager/`, `node/` y
  `portfolio/` por unidades completas.

Cada carpeta descubierta por `unittest` lleva `__init__.py`. Un traslado agrupa
un comportamiento completo, actualiza sus imports y comandos, y conserva el
recuento de casos; no se mezcla con cambios funcionales.

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
