# Auditoría ICTrading run 86 — 2026-07-30

## Alcance

- Memoria: `outputs/ubs_memory_ICTRADING_STANDARD.sqlite`
- Run: `86`
- Directorio: `outputs/ubs_agent/ICTRADING/STANDARD/run_20260730_050032`
- Configuración base: 2 generaciones, 30 semillas, 10 variantes por semilla,
  backtests habilitados, periodo `2020.01.01`–`2024.12.31`.

## Resultado del flujo

- Base: 600 candidatos; 31 aceptados, 473 rechazados, 88 sin operaciones,
  5 sin histórico y 3 sin reporte.
- Robustez (`2025.01.01`–`2026.06.01`): los 31 aceptados base fueron
  evaluados; 4 aceptados y 27 rechazados.
- Final Tick corto (`2026.05.01`–`2026.05.31`): los 4 aceptados de robustez
  fueron evaluados; 2 aceptados, 1 `pending_ohlc_trades` y 1 rechazado.
- Final Tick 6M (`2026.01.01`–`2026.06.30`): los 3 elegibles fueron
  evaluados y los 3 fueron rechazados.
- Pool válido resultante: 0 candidatos.
- Regresión: 0 filas, correctamente, porque solo son elegibles candidatos
  aceptados en Final Tick 6M.

La auditoría nativa analizó 993 reportes referenciados (run y semillas activas):
0 archivos ausentes, 0 errores de parseo, 0 diferencias de net profit,
0 cierres sin emparejar, 0 separadores numéricos mixtos y 0 discrepancias entre
métricas persistidas y reportes.

## Hallazgos operativos

Los tres `no_report` persistentes son:

- `AUDJPY M1`, candidato `45096`; existen dos snapshots de watchdog.
- `NSA.NYSE H1`, candidato `45257`.
- `VYNE.NAS H1`, candidato `45291`.

Los cinco `no_history` corresponden a `LOGC.US`, `XSPS.LSE`, `TAPT.NYSE`,
`SSE.LSE` y `BYU.NAS`. Son resultados clasificados, no huecos en la cadena.

## UseEveryTick

Se inspeccionaron los 654 `.set` del directorio del run:

- 600 generación, 31 copias aceptadas, 3 retry y 2 robustez:
  `UseEveryTick=false`.
- Final Tick corto: 5 copias OHLC con `false` y 5 copias real-tick con `true`.
- Final Tick 6M: 4 copias OHLC con `false` y 4 copias real-tick con `true`.
- 0 claves ausentes, 0 duplicadas, 0 valores fuera del contrato y 0 flags de
  optimización distintos de `N`.
- `mutated_keys` contiene 0 referencias a `UseEveryTick`.

Las 26 pruebas focalizadas de parámetros por etapa, elegibilidad 6M y regresión
terminaron correctamente.

## Cierre operativo del manager-node

El estado persistido del nodo `ictrading-standard-test` confirma:

- Job `20260730_050032_310500` en estado `completed`, código global `0`,
  `error=null`, `cleanup_failed=false`, PID final nulo y cola vacía.
- La petición tenía activados robustez, Final Tick, Final Tick 6M, regresión,
  reparación automática con 4 intentos y limpieza posterior.
- Reparación de resultados: comenzó con 12 `report_mismatch/no_report`; el
  primer intento resolvió 9 como rechazados válidamente evaluados. Los tres
  `no_report` restantes se reintentaron en los cuatro ciclos configurados sin
  generar reporte.
- Robustez: 31 pendientes en el primer intento y 2 reintentos adicionales;
  terminó sin pendientes.
- Final Tick corto: 4 pendientes iniciales y 1 reintento; terminó sin
  pendientes retryables.
- Final Tick 6M: los pendientes evolucionaron `2 -> 2 -> 1 -> 0`.
- Las etapas de calidad se omitieron porque su contador pendiente era cero.
- Regresión se omitió porque no hubo aceptados en Final Tick 6M.
- `cleanup_tester`, `cleanup_data` y `cleanup_verify` devolvieron código `0`.
  El verificador final registró que no quedaban datos históricos de MT5.

Los directorios llamados `run_86_pending` conservan las últimas copias de
trabajo por diseño: se recrean al iniciar cada intento y no representan trabajos
pendientes cuando la memoria y el estado del nodo tienen contador cero.

## Pendientes reales

- Investigar o tratar manualmente los tres `no_report`: `AUDJPY M1`,
  `NSA.NYSE H1` y `VYNE.NAS H1`. AUDJPY agotó también el reintento interno del
  watchdog de MT5.
- Decidir si se deshabilitan manualmente del universo los cinco símbolos
  clasificados `no_history`: `LOGC.US`, `XSPS.LSE`, `TAPT.NYSE`, `SSE.LSE` y
  `BYU.NAS`. El archivo de símbolos deshabilitados no los contiene; el flujo
  automático solo clasifica estos casos y la interfaz ofrece la desactivación
  como acción separada.

## Score, pesos y aprendizaje entre runs

- El `score` base se calcula por candidato y queda persistido en
  `candidates.score`, junto con sus métricas.
- Los resultados de robustez, Final Tick, Final Tick 6M y regresión se
  persisten por separado. El peso de feedback se recompone dinámicamente al
  seleccionar semillas, aplicando bonificaciones y penalizaciones según esas
  etapas; no existe una única columna de peso final congelado.
- Las decisiones quedan auditables en `generation_seed_selection`, incluyendo
  `selection_score`, pesos de activo y timeframe, diversidad, probabilidad del
  modelo, peso de fitness y evidencia.
- En el run 86 se registraron 60 decisiones (30 por generación), todas con
  componentes de peso y fitness. La primera selección de la generación 1, por
  ejemplo, tuvo `selection_score=36.740866`, compuesto por
  `asset_weight=28.453729`, `timeframe_weight=1.625531`,
  `diversity=4.802492` y `fitness_weight=12.394095` aplicado con escala `0.15`.
- Antes del run 86 había 1.098 resultados Final Tick 6M finalizados, de los
  cuales 325 eran positivos. Se superaban holgadamente los mínimos de
  entrenamiento (300 filas y 30 positivas), por lo que el modelo de fitness
  estaba realmente entrenado.
- El feedback de activo, timeframe y mutación consulta el histórico acumulado
  de candidatos. El modelo de fitness usa únicamente runs anteriores al run
  actual, evitando que el resultado del mismo run se filtre en su selección.
- Los tres rechazos Final Tick 6M del run 86 pasan a ser evidencia negativa
  para runs posteriores. Los casos técnicos sin resolución se excluyen del
  entrenamiento; `no_trades` con reporte real sí aporta penalización.
- Dos pruebas focalizadas del manager y 27 del agente verificaron el orden
  multicíclo, la limpieza al final de cada ciclo y las reglas de cálculo y
  exclusión del feedback.
