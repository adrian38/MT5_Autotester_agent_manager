# La portada compara la demo al lote de la cuenta real

## Caso observado (2026-10-14)

La portada del portafolio AXI 105 declaraba dos estrategias Tesla+ como
`NO REPRODUCE` y BTCUSD como `AVISO`, mientras la tabla de Análisis las daba
por `REPRODUCE` o `MEJOR QUE EL TESTER` al poner la demo al lote real.

No era una diferencia marginal:

- Tesla+: 17,73 EUR en tester a 5 lotes frente a 3,55 EUR en real a 1 lote;
  17,73 / 5 = 3,55, por lo que reproducía.
- La otra Tesla+: 30,04 EUR a 5 lotes frente a 11,90 EUR a 1 lote;
  normalizada, la demo era 6,01 EUR y la real era mejor.
- BTCUSD: 93,86 EUR a 0,09 frente a 58,79 EUR a 0,06; la demo comparable era
  62,57 EUR, no 93,86 EUR.

Las operaciones reales ya estaban atribuidas manualmente. La corrección de
`ATRIBUCIÓN PENDIENTE` no resolvía este caso porque el error restante era
comparar dinero producido con tamaños de posición distintos.

## Contrato

`dashboard_metrics.js` lleva cada operación demo a la escala real antes de
calcular PnL, drawdown y el veredicto de la estrategia:

1. si existe una pareja real, usa su volumen ejecutado;
2. si no hay pareja, usa `limits.volume_expected_real`, que el análisis publica
   también para las operaciones tester sin pareja;
3. si ninguno existe, conserva el PnL bruto como respaldo visible.

La operación real nunca se simula: conserva el dinero y lote ejecutados. Las
reales atribuidas a mano continúan sumando a su estrategia. Así la portada
responde a reproducción y no al tamaño de la posición, y sus totales Test/DD
usan la misma curva demo normalizada.

Es un cambio exclusivo del manager. No modifica la auditoría persistida ni el
runtime de ningún agente.
