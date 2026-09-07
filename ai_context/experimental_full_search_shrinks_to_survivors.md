# UBS experimental: la composición se encoge a los supervivientes (2026-09-07)

## Evidencia: dos generaciones a cinco minutos, solo cambia el flag

Guardadas en la memoria de la copia ICTrading autorizada
(`outputs/ubs_memory_ICTRADING_STANDARD.sqlite`). `metrics_json.inputs` de ambas
es idéntico salvo `experimental_full_search`.

| | #20 `experimental_full_search=True` | #21 `False` |
| --- | --- | --- |
| Log | `manager_full_history_generate_20260907_084142.log` | `..._20260907_094053.log` |
| Estrategias | 4 | 7 |
| Beneficio neto | 8.629 | 21.886 |
| Valley real / objetivo | 225,02 / 270 → **83,3 %** | 267,30 / 270 → 99,0 % |
| Lote / unidades | 0,62 / 17 | 2,16 / 63 |
| Antirrelleno 6M | **4** eliminadas de 8 | 1 eliminada de 8 |

Las dos primeras pasadas eligen 8 activas. La experimental pierde justo los tres
mayores contribuidores de la #21 —EURUSD H1 (8.236), US500 H1 (2.267) y USDJPY H1
(2.050), que suman 12.553 de los 13.257 de diferencia— y acaba con un 70,6 % en
Metals, con el 17 % del presupuesto de DD sin usar.

## Causa 1: el objetivo del torneo y la regla antirrelleno tiran en sentidos opuestos

- `_result_rank` (`mt5_manager/portfolio_full_experimental.py:224`) ordena por
  beneficio y, **como segunda clave, por `active_strategies`**: añadir un miembro
  mejora el rango aunque su aporte reciente sea despreciable.
- `_underrepresented_recent_allocation_ids` (`mt5_manager/portfolio_service.py:2119`)
  es una regla de **cuota**: `contribución < total * 5 %`. Cuanto más miembros
  tiene la composición, menor es la cuota de cada uno y más caen bajo el mínimo.
  Reproducido: con un núcleo al 60 % y satélites iguales, 4 miembros → 0 bajo el
  mínimo; 12 miembros → 11 bajo el mínimo.
- Nada del embudo experimental anticipa esa regla.
  `_segment_stability_key` usa `recent_net_profit_001 / recent_equity_dd_001`, un
  **ratio por estrategia**, no la cuota sobre el total al lotaje final. El avance
  entre rondas puntúa frecuencia de selección y `net_profit_contribution`
  histórico. `min_strategy_recent_contribution_pct` no llega al motor.

Así que el torneo propone composiciones que la regla siguiente destripa.

## Causa 2: el refinamiento solo encoge, y desde el 2026-09-07 no repone

`_optimize_without_recent_fillers` (`mt5_manager/portfolio_service.py:2146`)
reoptimiza únicamente las activas supervivientes: «inactive candidates cannot
enter». Es la corrección de `ubs_generation_repeated_tournaments.md`, correcta
para acotar el número de torneos, pero deja el resultado monótonamente
decreciente. Reproducido con callback sintético: pools `[486, 4]` — 486
candidatos disponibles, 4 usados en la segunda vuelta, ninguna reposición. Es
exactamente la secuencia del log de producción: «ronda 1, 486 candidatos» →
«final completa con 4 candidatos».

Con 1 relleno (ruta estándar) el recorte es inocuo; con 4 de 8 se pierde media
cartera y no hay forma de rellenar el DD liberado.

## Causa 3: la auditoría y la telemetría describen la reejecución, no el torneo

El refinamiento vuelve a entrar en `optimize_experimental_full_portfolio` con 4
candidatos. Ahí `pool_size = max(max_total_candidates, 2) = 100`, así que
`while len(current) > pool_size` no entra: `round_number = 0` y `best_result =
None`. Consecuencias, todas visibles en la #20 guardada:

- El aviso guardado dice «Búsqueda UBS experimental: 4/4 candidatos examinados;
  0 exposiciones clasificatorias en 3 rotaciones; 1 lotes viables, 0 no viables;
  0 ronda(s)». El torneo real —486 candidatos, 3 rondas, 15 lotes, 09:00→09:22—
  desaparece del registro. `_locked_full_proposals:2318` copia los avisos de la
  **última** llamada.
- `experimental_full_history_stability` se recalcula sobre los 4 supervivientes y
  da `passed: true`, «OK», avalando una cartera con un 60 % menos de beneficio
  que la estándar.
- Se descarta el `best_result` del torneo real: si alguna ronda encontró una
  composición mejor, ya no se puede recuperar.
- La optimización profunda y la diversificación acaban corriendo sobre el pool de
  4 («no encontro mejora valida tras 16 intento(s), pool 4 candidato(s)»).

## Alcance

El cálculo corre en el manager Docker (`/app/mt5_manager/portfolio_service.py`),
no en el fork `manager_node_runtime`: no hay problema de paridad de nodo aquí.
`_optimize_without_recent_fillers` **es primitiva compartida con mensual**
(`portfolio_monthly_service._monthly_proposals:118`), que sigue congelado; el
motor experimental completo y `experimental_monthly_search` son módulos
separados.

Las 5 pruebas de `tests/test_portfolio_full_experimental.py` pasan: ninguna cubre
la interacción entre el motor y la regla de cuota. Revisión sin modificar código
ejecutable.

## Descartado: meter la cuota en el ranking

La primera propuesta fue incluir la cuota de aporte reciente en `_result_rank` y
en la clave de avance. **Es incompatible con el objetivo del modo.** La cuota al
lotaje final favorece a los caballos de batalla con muchas unidades, así que
penalizar la cuota baja empuja el torneo hacia composiciones concentradas, justo
lo contrario de los pools diversificados. Y `active_strategies` como segunda
clave de `_result_rank` no es un defecto: es el objetivo —premiar amplitud—. Con
ese cambio la búsqueda experimental converge a los mismos workhorses que ya
encuentra la estándar y se queda en una reejecución carísima de ella.

Lo protege `test_result_rank_still_rewards_breadth_over_concentration`: a igual
beneficio, la composición amplia debe seguir ganando.

## Corrección aplicada (2026-09-07)

Todo dentro de la ruta opt-in. La regla conserva su significado —el 5 % del
usuario no cambia— y `_optimize_without_recent_fillers` no se toca, así que el
mensual congelado y la ruta estándar quedan igual (el grafo confirma sus cinco
llamadores intactos).

1. **La regla se aplica donde se puede reponer.**
   `_refined_without_recent_fillers` (`mt5_manager/portfolio_full_experimental.py:302`)
   quita los rellenos y reoptimiza sobre el **lote ganador menos los rellenos**,
   no sobre los supervivientes: entran candidatos que la pasada anterior no
   eligió y el DD liberado se vuelve a usar. La amplitud sobrevive a la regla en
   vez de cambiarse por ella.
2. **Los finalistas se comparan después de aplicar la regla.** Se refinan la
   final completa y el mejor del torneo, y `_result_rank` decide sobre el
   resultado que se va a entregar. La amplitud que sobrevive gana; la que la
   regla destruiría deja de ganar sobre el papel. `_result_rank` no se modifica.
3. **La regla no se reimplementa.** Se inyecta desde `portfolio_service` como
   `recent_filler_ids`, ligada a `_underrepresented_recent_allocation_ids`, para
   que motor y llamador no puedan separarse. Parámetro opcional: sin él el motor
   se comporta como antes.
4. **La telemetría es la del torneo real.** `_locked_full_proposals` captura
   avisos y auditoría de la **primera** pasada (`experimental_telemetry`), así
   que una reejecución sobre supervivientes ya no puede sustituirlas por
   «0 ronda(s)» con la auditoría de la composición recortada.

Por qué esto no reabre el bucle de `ubs_generation_repeated_tournaments.md`: el
reintento vive **dentro** del motor, donde el callback es una sola optimización
de un lote de ≤100, no el torneo completo. Está acotado además por
`EXPERIMENTAL_FULL_ANTIFILLER_RETRIES = 3`. El desastre de ayer venía de que el
callback era el torneo entero. Si tras los reintentos aún quedan rellenos, la
primitiva compartida del llamador sigue siendo la red de seguridad.

## Verificación

Cinco pruebas nuevas en `tests/test_portfolio_full_experimental.py`, las tres
centrales validadas por mutación (fallan contra el comportamiento anterior):

| Prueba | Mutación que la hace fallar |
| --- | --- |
| `test_recent_fillers_are_replaced_from_the_winning_pool` | reponer solo desde supervivientes → faltan `set-4`/`set-5`, beneficio 960 en vez de 1730 |
| `test_finalists_are_ranked_after_the_recent_contribution_rule` | rankear antes de la regla → gana la de 1500 que la regla deja en un miembro |
| `test_the_tournament_record_survives_a_survivor_rerun` | telemetría de la última pasada → se pierde «486/486 candidatos» |
| `test_antifiller_retries_are_bounded` | — acota los reintentos a 1+3 optimizaciones |
| `test_result_rank_still_rewards_breadth_over_concentration` | — guarda del objetivo |

474 pruebas de `python -m unittest discover -s tests` en verde. El fork del nodo
no tiene esta ruta: buscando por texto del mensaje y por símbolo en
`manager_node_runtime/` de la copia ICTrading no aparece nada, y
`test_node_runtime_fork_parity` no la cubre. No hay nada que portar.

Pendiente de comprobación en producción: hace falta cargar la imagen corregida y
relanzar una generación con `experimental_full_search` para ver el resultado
sobre las 486 candidatas reales. Editar el fichero no actualiza una ejecución en
curso.
