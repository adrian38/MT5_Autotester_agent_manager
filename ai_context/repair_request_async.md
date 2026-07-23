# Envio asincrono de reparaciones desde el manager

- El manager responde inmediatamente al `POST /api/nodes/{id}/repair` con HTTP
  202 y envia la peticion real al nodo desde un hilo en segundo plano.
- El nodo conserva su implementacion y contrato actuales; no necesita cambios.
- La llamada en segundo plano permite hasta una hora para que el nodo termine su
  preflight sincrono. Los errores posteriores se registran en stderr del manager.
- El modal de reparacion carga primero `GET /api/nodes/{id}/runs?limit=100`.
  Esa llamada es de solo lectura pero puede tardar mas que el timeout generico
  en nodos remotos, asi que el manager la proxya con timeout de 120 segundos.
