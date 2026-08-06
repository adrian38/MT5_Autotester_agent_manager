# DD de cartera después del redondeo ejecutable

## Hallazgo reproducido en AXI el 2026-08-05

La generación UBS A/M/C full-history puede terminar con
`Final portfolio violates valley DD` aunque la asignación optimizada estuviera
dentro del límite antes de preparar los `LotPerBalance_step`.

Caso observado con capital 1000, DD valle 8 %, reserva configurada 10 % y base
conservadora:

- el límite efectivo agresivo es 72 (reserva 10 %);
- el moderado usa 68 (reserva mínima 15 %);
- el conservador usa 60 (reserva mínima 25 %);
- el moderado optimizado dio DD 66,06 antes del plan ejecutable;
- `_execution_plan_allocations` redujo unidades que el step entero del EA no
  podía representar y la reevaluación posterior superó 68;
- `optimize_portfolio` lanzó entonces `Final portfolio violates valley DD`;
- `_locked_full_proposals` exige las tres variantes, por lo que el paquete
  completo quedó `FAILED` aunque agresivo y conservador fueran viables.

Reducir una unidad no garantiza reducir el DD cerrado combinado: también puede
retirar beneficio que cubría temporalmente la caída de otra curva. En la misma
reproducción el agresivo pasó de DD 70,00 antes del redondeo a 71,64 después,
aunque siguió por debajo de 72.

## Implicaciones

- No es corrupción de históricos ni ausencia de candidatos: se cargaron 234
  sets válidos.
- Los portafolios históricos cercanos a DD 70 son compatibles con el límite
  agresivo de 72; no prueban que el moderado deba aceptar DD 70.
- El defecto es un caso límite latente del postprocesado ejecutable. Depende de
  la composición elegida y por eso otras generaciones, incluso sin búsqueda
  experimental, pueden finalizar bien.
- La primitiva afectada está en `portfolio_manager/ubs_portfolio.py` y la usan
  UBS completo, mensual y Grid. Cualquier reparación debe preservar la
  separación de orquestación indicada en `portfolio_ubs_parity.md`.

## Cobertura de regresión

La regresión `test_execution_rounding_repairs_dd_without_losing_required_sets`
cubre el caso donde el redondeo reduce una curva que actúa como cobertura,
eleva el DD combinado y obliga a reparar la asignación ejecutable sin eliminar
sets requeridos ni devolver una cartera fuera del límite.

## Solución aplicada

`optimize_portfolio` pasa el resultado por `_repair_executable_allocations`
después de convertirlo a `LotPerBalance_step`. Si el plan ejecutable viola el
DD, la reparación prueba reducciones ejecutables, conserva los sets protegidos
y el mínimo de estrategias, y elige primero una solución válida con mayor net;
mientras ninguna sea válida sigue la menor ratio de violación hasta llegar a
una composición factible o agotar las reducciones permitidas. Cada ajuste queda
en el log como `reduce_unit_for_execution_dd`.

La reproducción de producción AXI con los mismos 234 sets y ajustes del fallo
terminó con tres propuestas: agresivo 71,64/72, moderado 66,39/68 tras una
reducción de reparación, y conservador 59,63/60; las tres conservaron los seis
sets comunes.
