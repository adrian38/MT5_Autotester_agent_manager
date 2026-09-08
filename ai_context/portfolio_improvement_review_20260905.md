# Revisión de «Mejorar base» UBS normal (2026-09-05)

## Alcance confirmado por el usuario

Cuando el usuario habla de portafolio sin especificar ámbito, se refiere a UBS
normal (`full_history`). El mensual sigue congelado conforme a
`monthly_portfolio_frozen.md`; no modificarlo sin petición explícita.

La tarea comenzó como revisión y el usuario después pidió seleccionar solo el
modo elegido y guardar la mejora como otro portafolio identificado por origen y
modo. Esa implementación está documentada al inicio de
`portfolio_saved_base_improvement.md`. No se modificaron memorias reales.

## Hallazgos reproducidos

1. **La mejora omite el mínimo de contribución reciente guardado.** La generación
   normal usa `_underrepresented_recent_allocation_ids` y
   `_optimize_without_recent_fillers` con `min_strategy_recent_contribution_pct`
   (5 % por defecto). La orquestación de mejora llama directamente al optimizador
   y su auditoría exige solamente beneficio reciente positivo. Una prueba
   controlada con contribuciones 6M de 1000 y 1, una unidad por estrategia y
   ganancia histórica beneficio/DD del 30 % obtiene `ACEPTADA`; la primitiva
   normal marca la incorporación de 1 (0,0999 % del total) como relleno.
   Una corrección debe evaluar las nuevas incorporaciones al lotaje final de
   las variantes A/M/C, conservando las originales bloqueadas; no reutilizar
   ciegamente una rutina que pueda eliminar originales.

2. **Se prioriza el número de incorporaciones frente a comparar eficiencia.**
   `generate_full_history_improvement` prueba desde el máximo hacia uno y retorna
   al primer éxito. Con un doble controlado que entrega +4 % para dos y +20 %
   para una, solo se llama al intento de dos. Esto demuestra el cortocircuito,
   no que esos porcentajes hayan ocurrido en una cartera real. Además, un
   rechazo de auditoría reduce el número pedido: no explora automáticamente
   otra composición del mismo tamaño. No interpretar el error final como prueba
   exhaustiva de que no existe una mejora.

3. **El mínimo explícito de 0 % se convierte en 3 %.**
   `improvement_options` usa `float(inputs.get(...) or 3.0)`, aunque el diálogo
   permite cero y la validación declara el intervalo 0–25. Reproducido llamando
   a la función con `improvement_min_efficiency_gain_pct=0`.
   Esta primitiva es compartida con mensual: cualquier arreglo debe respetar la
   congelación y limitar el cambio de comportamiento a UBS normal.

## Límites de la revisión

Los tres hallazgos quedaron corregidos exclusivamente para UBS normal. La
selección del modo, el aislamiento de los otros modos, la reserva guardada, la
comparación entre cantidades, el mínimo 6M y la persistencia independiente tienen
pruebas en `test_portfolio_improvement_selected_mode.py`; el nodo ICTrading tiene
su propia prueba de inserción, conservación del original y reintento idempotente.
La búsqueda no explora exhaustivamente todas las composiciones del mismo tamaño.

`codebase-memory-mcp` se indexó y se consultó al inicio; durante el trabajo dejó
de responder con `Transport closed`, incluido el intento final de reindexación.
La revisión posterior se verificó sobre fuente directa y pruebas, sin presentar
el grafo inicial como actualizado.

Las 16 pruebas focalizadas existentes de `tests.test_portfolio_improvement`
pasaron antes de los experimentos. No cubren los tres hallazgos anteriores.
Los experimentos ejecutaron funciones reales con entradas sintéticas y dobles
controlados para correlaciones/selección; no constituyen un backtest ni una
reproducción de un portafolio concreto del usuario. No se encontró un log de
`improve` en `portfolio_logs` de la copia local ICTrading consultada.
