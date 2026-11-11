"""`python -m mt5_manager.node`, que es el comando que documenta el README.

El arranque sigue viviendo en `node.py`; esto solo existe para que el comando no
cambie al convertirse `node` en paquete.
"""
from .node import main

raise SystemExit(main())
