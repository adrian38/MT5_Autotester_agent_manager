"""`python -m mt5_manager.manager`, que es el comando que arranca el manager.

Lo usan el `Dockerfile` a traves de `docker_entrypoint`, `scripts/run_manager.bat`
y el README. El arranque sigue viviendo en `manager.py`; esto solo existe para
que el comando no cambie al convertirse `manager` en paquete.
"""
from .manager import main

raise SystemExit(main())
