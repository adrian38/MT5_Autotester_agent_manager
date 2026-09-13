# Comparación de correlación entre portafolios guardados

- La cabecera del manager abre `/correlation.html` mediante el botón
  `Correlación`, situado junto a `Experimenta`.
- La pantalla es estrictamente de lectura. Reutiliza `/api/nodes` y las rutas
  existentes de lista/detalle de portafolios; no escribe en las memorias UBS y
  no requiere cambios en `manager_node_runtime/`.
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
