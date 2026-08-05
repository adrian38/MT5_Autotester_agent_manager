# Portafolio Grid UBS

## Decisión de dominio

- El alcance persistido es `grid`; nunca se convierte implícitamente a `full_history`.
- El pool exige las cuatro etapas aceptadas que ya aplica `PortfolioSource.candidate_rows` y, además, `EnableGrid=true` explícito en el `.set`.
- `EnableGrid=false`, ausencia de la clave o un archivo ilegible dejan el candidato fuera del pool Grid.
- Cada `.set` puede recibir tantas unidades ejecutables como permita el valle del portafolio. No existe un tope oculto de unidades por set, por símbolo o total; el número de estrategias activas y sus lotes son resultados del optimizador.
- El riesgo se limita exclusivamente por el valle del portafolio. No se usan `GridLossUSD`, `StopTradingAfterGridLoss`, `CloseAtMinEquity`, `PropFirmMaxDailyDD` ni otros stops internos para elegibilidad o dimensionamiento.
- El valle Grid se calcula como `max(DD flotante máximo, DD cerrado de la curva combinada)` y debe respetar el valle objetivo.
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
- Toda respuesta HTTP usa JSON compatible con navegador. Los valores no finitos de auditoría se convierten a `0.0` antes de persistirse o cruzar la red, evitando `NULL` en columnas históricas `NOT NULL`.
