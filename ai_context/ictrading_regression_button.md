# Botón de prueba regresiva de ICTrading

- La acción aparece exclusivamente en tarjetas cuyo `node.broker`, normalizado a mayúsculas, sea `ICTRADING`.
- Usa un diálogo propio para elegir uno o más runs terminados; no comparte opciones ni ejecución con Reparar.
- El navegador envía `POST /api/nodes/<id>/regression` con `{ "run_ids": [...] }`.
- El manager reenvía la petición al nodo como `POST /api/v1/jobs/regression`.
- Reparar conserva intacto su flujo completo y sus reintentos.
