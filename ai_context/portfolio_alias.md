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
- La exportación escribe `Alias:` en el resumen y la importación lo reinserta en
  todas las propuestas reconstruidas, por lo que la etiqueta vuelve con el
  portafolio sin alterar su identidad técnica.
