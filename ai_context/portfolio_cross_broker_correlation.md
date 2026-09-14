# Comparación de correlación entre portafolios guardados

- La cabecera del manager abre `/correlation.html` mediante el botón
  `Correlación`, situado junto a `Experimenta`.
- La pantalla es estrictamente de lectura. Usa `/api/nodes` para descubrir los
  nodos y `/api/correlation/nodes/<id>/portfolios` para lista/detalle; no escribe
  en las memorias UBS y no requiere cambios en `manager_node_runtime/`.
- En `dev`, esas rutas usan `ExperimentCoordinator.readonly_source`, por lo que
  AXI y RoboForex se leen desde los mismos mounts CIFS de solo lectura
  `/experiment-data/*` que Experimenta. Las rutas operativas `/data/*` y las
  pantallas UBS/mensual/Grid normales no cambian. Fuera del override de Compose
  la fuente conserva la configuración normal del nodo.
- Grid no vive en la memoria del broker: Correlación lee la base ya existente de
  `runtime/grid_portfolios` y, si no existe para un nodo, devuelve una lista
  vacía sin crearla.
- Consulta los ámbitos `full_history`, `monthly` y `grid` de todos los nodos.
  Los bundles A/M/C se seleccionan como un portafolio y, una vez leído el
  detalle, permiten elegir la variante concreta.
- La medida reproduce el convenio vigente de
  `curve_increment_correlation`: Pearson sobre incrementos consecutivos y
  relleno con ceros de la serie más corta. No se correlacionan niveles
  acumulados. Esta compatibilidad es importante porque los portafolios antiguos
  no guardan un eje de fechas común.
- La visualización combina una matriz de calor de dominio fijo `[-1, 1]`, las
  curvas acumuladas expresadas como PnL/capital y una tabla de pares ordenada por
  dependencia absoluta. La UI advierte que el eje es posición relativa, no
  calendario, y que correlación no implica causalidad ni garantiza
  diversificación futura.
