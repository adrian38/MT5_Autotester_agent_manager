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
