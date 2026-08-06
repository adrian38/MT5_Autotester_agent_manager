# Portafolio Grid UBS

## Decisión de dominio

- El alcance persistido es `grid`; nunca se convierte implícitamente a `full_history`.
- El pool exige las cuatro etapas aceptadas que ya aplica `PortfolioSource.candidate_rows` y, además, `EnableGrid=true` explícito en el `.set`.
- `EnableGrid=false`, ausencia de la clave o un archivo ilegible dejan el candidato fuera del pool Grid.
- Cada `.set` puede recibir tantas unidades ejecutables como permita el valle del portafolio. No existe un tope oculto de unidades por set, por símbolo o total; el número de estrategias activas y sus lotes son resultados del optimizador.
- El riesgo se limita exclusivamente por el valle del portafolio. No se usan `GridLossUSD`, `StopTradingAfterGridLoss`, `CloseAtMinEquity`, `PropFirmMaxDailyDD` ni otros stops internos para elegibilidad o dimensionamiento.
- El valle Grid se calcula como `max(DD flotante máximo, DD cerrado de la curva combinada)` y debe respetar el valle objetivo.
- El término flotante de esa regla es la **exposición abierta agregada del peor día**, medida alineando en el tiempo las operaciones de cada estrategia, con el flotante declarado (`DD equity − DD balance`) por unidad como **suelo**. El `max()` entre estrategias que se usaba antes daba por supuesto que sólo una está bajo el agua en cada momento; medido sobre el paquete #4 de RoboForex, cuatro estrategias coincidían el 2025‑04‑07 con 279,40 frente a los 227,18 que declaraba la peor. No se usa la suma de los peores de cada una (641,98): eso supondría que todas tocan fondo el mismo día, y sus peores días son distintos.
- El margen Grid es **vinculante y se mide sobre el pico simultáneo de la escalera**, no sobre una posición por unidad. Un grid escalona el lote internamente: `USTECHCash_M15` tiene pierna base 0,01 y llega a 7 piernas y 0,95 lotes abiertos a la vez, ×95 el margen que contabilizaba el modelo compartido. La poda reduce unidades cuando el margen de pico no cabe, igual que cuando no cabe el valle.
- El solapamiento entre grids se mide sobre los **días con posiciones abiertas**, no sobre el P/L cerrado diario: un grid cierra ganadoras y deja abiertas las perdedoras, así que su curva cerrada es suave y positiva por construcción y los filtros de correlación compartidos son ciegos al riesgo que importa. `max_open_overlap` (0,60 por defecto) descarta del pool el menos eficiente de cada par que comparte sus días bajo el agua.
- Ese criterio **sustituye** a los umbrales de correlación de curva cerrada, que Grid anula (`max_pair_corr`, `max_downside_corr`, `max_dd_overlap`, `max_portfolio_corr` a `None` en `normalize_grid_settings`). No sólo eran ciegos: como todos los grids se parecen en esa curva, se rechazaban entre sí y estrangulaban el portafolio. Medido en RoboForex, dejaban el Moderado en 2 estrategias con el valle al 73% de su límite; sin ellos son 4 con el riesgo igual de vinculante. La casilla «Evitar grids hundidos los mismos días» (`use_correlation`) gobierna ahora el criterio nuevo. UBS y mensual conservan sus umbrales intactos, y hay un test que lo fija.
- Consecuencia asumida: al anular `max_portfolio_corr`, las curvas de paquetes Grid ya guardados dejan de vetar candidatos. Lo que evita repetir sets entre paquetes es `exclude_used_sets`.
- El bootstrap de estrés Grid corre sobre la equity diaria (cerrado acumulado menos exposición abierta), no sobre la curva cerrada.
- La variante Agresiva **selecciona por eficiencia** como las otras dos. Su identidad son la reserva menor y los límites de grupo más holgados, que viajan explícitos al optimizador compartido. Puntuar por ganancia absoluta bajo la regla `max()` era contraproducente: añadir un set cuyo flotante queda por debajo del máximo vigente no cuesta valle, así que el criterio de eficiencia recoge más beneficio con el mismo riesgo. La siembra por grupo se limita a 2 unidades porque sembrar 5 multiplica por 5 el flotante de ese set antes de medir nada.
- Todo lo anterior vive en `portfolio_manager/grid_risk.py` y sus dos consumidores Grid. `ubs_portfolio.py` y `portfolio_service.py` no cambian.
- El margen continúa siendo una validación de viabilidad operativa, no un segundo límite de pérdida.

## Separación

- `portfolio_manager/grid_set.py`: lectura y filtros de `.set`.
- `portfolio_manager/grid_portfolio.py`: regla de riesgo y optimización Grid.
- `mt5_manager/portfolio_grid_service.py`: carga, propuestas e inventario Grid.
- `mt5_manager/static/portfolios_grid.html` y `portfolios_grid.js`: interfaz independiente.
- Las primitivas UBS compartidas siguen concentradas en `ubs_portfolio.py` y `portfolio_service.py`.

## Compatibilidad

`ubs_portfolio.py` conserva wrappers para `set_file_has_enabled_grid` y `filter_rows_grid_off`, de modo que los consumidores históricos no cambian de contrato. Los nuevos consumidores deben importar desde `grid_set.py`.

## Validación RoboForex ECN (2026-08-04)

- La tarjeta `roboforex-ecn-192-168-1-152` encontró 43 filas Grid y cargó 40 estrategias lógicas en la captura validada.
- El DD flotante Grid se obtiene como la excursión abierta del reporte: `max(DD equity - DD balance, 0)`; entre estrategias activas se usa el máximo, nunca la suma.
- Los límites internos del EA quedan fuera del dimensionamiento. El DD flotante sí es vinculante mediante la regla de máximo anterior.
- El menor riesgo ejecutable observado fue `max(31.08 flotante, 13.01 cerrado) = 31.08`. Con capital 1000, valle solicitado 3% y reserva configurada 10%, los mínimos nominales resultan 3.4533% (Agresivo), 3.6565% (Moderado) y 4.1440% (Conservador), según la reserva efectiva de cada perfil.
- Cuando el porcentaje solicitado queda por debajo del lote mínimo ejecutable, no se deja la pantalla vacía: se construye cada variante con su menor porcentaje viable, se marca como ajuste automático y el valor ajustado se conserva únicamente si el usuario guarda esa propuesta.

## Interfaz y transporte

- Las tres variantes se comparan mediante tarjetas seleccionables y comparten una sola tabla de detalle. El nombre de archivo `.set` es el identificador principal; ruta, cuenta/candidato, símbolo y timeframe se muestran por separado.
- El proceso de cálculo mantiene visibles estado, barra de avance, etapa actual y acceso al log. Al finalizar, la pantalla recarga las propuestas sin depender de una recarga manual.
- Los portafolios Grid guardados tienen panel maestro/detalle con acciones independientes para reoptimizar, deshacer, exportar el paquete de sets en ZIP, borrar y abrir reportes. Dentro del detalle se cambia entre Agresivo, Moderado y Conservador sin mezclar sus tablas de sets.
- Guardar es una operación A/M/C atómica: persiste las tres variantes Grid en una sola fila `grid_bundle`, aunque tengan composiciones y lotajes diferentes. Asignaciones, auditoría y métricas conservan su `variant_key`.
- La persistencia Grid vive en `runtime/grid_portfolios/<node_id>.sqlite`, propiedad del manager. No se envía al endpoint antiguo del broker porque esos nodos convierten el scope desconocido a `full_history`; los ficheros y reportes del broker siguen siendo la fuente de solo lectura para cálculo y exportación.
- Excluir y reintegrar estrategias funciona igual que en el Portafolio UBS: desde una propuesta se pone el set en cuarentena, y desde un paquete guardado (individual o selección múltiple) se pone en cuarentena y se borra el paquete A/M/C entero, sin recalcularlo. La tabla «Estrategias excluidas» permite reintegrarlas.
- La exclusión Grid es enteramente del manager: la cuarentena se escribe en `runtime/grid_portfolios/<node_id>.sqlite`, junto a los paquetes. El endpoint `/api/v1/portfolios/exclude` del nodo **no sirve** para Grid porque exige un `portfolio_id` que exista en la memoria del broker; al reenviárselo respondía «Falta el portafolio que contiene las estrategias». `PortfolioSource.exclude_strategy` acepta por eso un `memory` de destino, sin cambiar el comportamiento de UBS ni del mensual.
- Consecuencia deliberada: la cuarentena Grid solo afecta a Grid, mientras que la cuarentena del broker escrita desde las pantallas UBS sigue aplicando también a Grid, porque `_calculation_source("grid")` incluye ambas memorias.
- La clave de cuarentena lleva la etiqueta de la memoria que la guarda (`BROKER/CUENTA/GRID`), así que reintegrar exige resolverla con `_calculation_source` del ámbito, no con la fuente del nodo.
- Cualquier exclusión o reintegración invalida las propuestas cacheadas de los tres ámbitos.
- Toda respuesta HTTP usa JSON compatible con navegador. Los valores no finitos de auditoría se convierten a `0.0` antes de persistirse o cruzar la red, evitando `NULL` en columnas históricas `NOT NULL`.
