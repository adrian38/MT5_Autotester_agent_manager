# Alias adicional de Portafolio UBS

- El alias es una etiqueta humana opcional; no sustituye `portfolios.id`, `name`,
  `portfolio_uid` ni el linaje de mejora.
- Se guarda en `metrics_json.inputs.portfolio_alias` para evitar una migración de
  esquema y para que sobreviva a reoptimización/completado, que reconstruyen los
  inputs guardados.
- Solo pertenece a `full_history`. El mensual continúa congelado y Grid mantiene
  su interfaz independiente.
- La escritura remota la ejecuta el nodo propietario mediante
  `POST /api/v1/portfolios/alias`. En `dev`, la implementación efectiva está en la
  copia ICTrading `manager_node_runtime/`; `mt5_manager/node.py` es únicamente el
  nodo local de pruebas.
- El texto se recorta en los extremos, colapsa espacios, admite borrado con cadena
  vacía y tiene un máximo de 80 caracteres.
- La portada también lo muestra y lo deja editar en cada tarjeta. Lo lee por
  `GET /api/nodes/<id>/portfolio-aliases?ids=…`, que devuelve solo las etiquetas
  de los portafolios pedidos: el sondeo de 60 s no puede abrir la memoria del
  agente, y `saved_portfolios` arrastraría `metrics_json` de la tabla entera.
  El navegador lo pide una vez por nodo y repinta al guardar; la escritura es la
  misma de siempre, `POST …/portfolio-manager/alias`.
- La exportación escribe `Alias:` en el resumen y la importación lo reinserta en
  todas las propuestas reconstruidas, por lo que la etiqueta vuelve con el
  portafolio sin alterar su identidad técnica.
