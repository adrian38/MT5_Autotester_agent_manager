# Búsqueda mensual experimental de candidatos

## Decisión

La generación mensual conserva su ruta UBS actual por defecto. El ajuste
`experimental_monthly_search` activa una búsqueda alternativa y opt-in definida
en `mt5_manager/portfolio_monthly_experimental.py`.

## Objetivo

Evitar que `top_k_per_symbol` y `max_total_candidates` dejen estrategias
mensuales elegibles sin llegar nunca al asignador UBS. La alternativa:

1. Ordena con varias lentes del mes objetivo: score UBS, beneficio, R/DD,
   consistencia entre años y riesgo.
2. Ejecuta tres rotaciones deterministas por ronda. Cada candidato aparece una
   vez en cada rotación, pero cambia de compañeros para reducir la dependencia
   de un único lote afortunado.
3. Forma los lotes minimizando la máxima correlación positiva entre sus curvas:
   Pearson, correlación bajista y solapamiento de drawdown.
4. Ejecuta cada lote sin el recorte preliminar top-K y con un presupuesto barato
   en las rondas clasificatorias; reserva la búsqueda completa para la final.
5. Hace avanzar aproximadamente la mitad según frecuencia de selección,
   contribución, consistencia y pertenencia al archivo Pareto beneficio/DD.
6. Repite hasta formar un pool final y conserva el mejor resultado viable.
7. Audita el resultado dejando fuera, uno a uno, hasta cinco años recientes.
   Esta auditoría mide beneficio fuera de muestra, cumplimiento de DD y
   estabilidad de la selección. Por ahora informa `OK`/`REVISAR`, pero no veta
   automáticamente una cartera si el histórico es escaso o inestable.

La validación, DD, margen, correlaciones, serialización y persistencia siguen
usando las primitivas UBS existentes. Si se activa la validación mensual
estricta, cada lote se limita a 40 candidatos para respetar el límite interno
del optimizador estricto.

## Interfaz y compatibilidad

El checkbox solo existe en `portfolios_monthly.html/js`. Su valor por defecto
es `false`, también para carteras antiguas, por lo que no cambia resultados ni
tiempos de la ruta estable mientras permanezca desmarcado.

El resultado añade advertencias de auditoría con candidatos examinados,
exposiciones, lotes viables/no viables, rondas ejecutadas y resumen
leave-one-year-out. El detalle de cada fold se conserva en
`seasonal_validation.experimental_leave_one_year_out`. La opción puede aumentar
considerablemente el tiempo de cálculo.
