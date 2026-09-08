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

## Comprobación en producción: el portafolio 22 aún se encoge (2026-09-07)

La imagen corregida sí se ejecutó. El portafolio guardado #22 conserva la
telemetría del torneo real (507/507 candidatos, 3 rondas) y registra 9 rellenos
sustituidos. Sin embargo, la auditoría experimental declara 8 estrategias y el
resultado persistido contiene sólo 6. Frente al control estándar #21, baja de
7 a 6 estrategias y de 21.885,68 a 13.882,76 de beneficio, aunque usa 96,9 %
del DD objetivo.

El log `manager_full_history_generate_20260907_123957.log` demuestra el flujo:

- las líneas 839-841 consumen los tres reintentos del final completo;
- las líneas 842-844 consumen los tres reintentos del otro finalista;
- todavía quedan 2 rellenos, y la línea 845 entra en la primitiva compartida;
- ésta conserva sólo 6 supervivientes y vuelve a llamar al motor experimental,
  que ya no ejecuta torneo porque 6 es menor que el lote máximo de 100.

La causa es que `_refined_without_recent_fillers` devuelve el último resultado
al agotar `EXPERIMENTAL_FULL_ANTIFILLER_RETRIES = 3` sin comprobar la
postcondición `recent_filler_ids(result) == set()`. Después,
`_optimize_without_recent_fillers` cumple la regla mediante su refinamiento
monótonamente decreciente. Así reaparece exactamente el encogimiento que el
arreglo experimental pretendía evitar; además, la auditoría y los avisos
guardados describen la composición experimental de 8, no la composición final
de 6.

Las 10 pruebas focalizadas anteriores pasaban, pero
`test_antifiller_retries_are_bounded` solamente comprobaba el máximo de
llamadas. No exigía que el resultado entregado quedase sin rellenos ni cubría
una cadena real que necesitase un cuarto reemplazo.

## Corrección aplicada tras el portafolio 22

`_refined_without_recent_fillers` ya no usa un máximo arbitrario de tres
reintentos. Cada vuelta elimina al menos un candidato y reoptimiza un único lote
estrictamente menor; el tamaño inicial del lote es el límite superior mecánico.
El motor no devuelve un finalista mientras `recent_filler_ids(result)` siga
teniendo elementos. Si un lote no puede reducirse o no admite reposición, ese
finalista se descarta y se prueba el otro; si ninguno cumple la regla, la
generación falla explícitamente en lugar de guardar una composición distinta de
la auditada.

La regresión
`test_antifiller_retries_continue_until_clean_and_shrink_the_pool` necesita más
de los tres intentos antiguos, comprueba la secuencia estrictamente decreciente
`12 → 10 → 8 → 6 → 4 → 2 → 1` y exige cero rellenos en el resultado. Pasan las
10 pruebas del módulo, las 69 de integración full/mensual y las 474 del suite
completo. No se modifica el mensual ni el fork del nodo: este cálculo lo ejecuta
el manager Docker.

## Incidente de duración y límite operativo (2026-09-07)

La siguiente generación, log
`manager_full_history_generate_20260907_163635.log`, parecía no terminar. No
había llegado aún al refinamiento antirrelleno: tras cargar 852 sets y aceptar
555, la primera ronda del torneo tardó unos 22 minutos (16:55:49–17:18:06 UTC)
y entonces inició la segunda con 278 candidatos. El proceso seguía avanzando;
el coste estaba dentro de los lotes clasificatorios.

Cada candidato ya participa en tres rotaciones con compañeros distintos, pero
cada lote clasificatorio heredaba además un `search_restarts` de hasta 1. Eso
duplicaba el trabajo de todas las rotaciones y rondas sin ampliar la cobertura
de candidatos. La ruta experimental ahora fuerza `search_restarts=0` y mantiene
`run_local_search=False` en los lotes clasificatorios. Los reinicios y el
refinamiento profundo quedan reservados para la optimización final completa.

También se sustituye el límite antirrelleno dependiente del tamaño del pool por
`EXPERIMENTAL_FULL_ANTIFILLER_RETRIES = 8`. Esas pasadas de reposición se hacen
sin reinicios ni refinamiento profundo. Si ocho reducciones no bastan, el
finalista se rechaza explícitamente: nunca se guarda una cartera con rellenos ni
se agota silenciosamente un pool grande.

La prueba del torneo exige ahora cero reinicios y ausencia de refinamiento
profundo en cada clasificación, conservándolos en la final. Una regresión con
30 candidatos y un relleno permanente exige fallo tras exactamente ocho
reposiciones. Pasan 11 pruebas focalizadas, 69 de integración full/mensual y
475 pruebas del suite completo. La ejecución que ya estaba dentro del
contenedor conserva el código de su imagen y no recibe este cambio hasta
reconstruir/reiniciar el manager.

## La ruta estándar cayó en el mismo encogimiento (2026-09-08)

La premisa del apartado «Corrección aplicada» —«con 1 relleno (ruta estándar) el
recorte es inocuo»— caducó al día siguiente. Con la búsqueda experimental
**apagada**, tres generaciones consecutivas cortaron **4 de 8**:

| Generación (log) | Elegibles | Antirrelleno | Sets | Neto |
| --- | --- | --- | --- | --- |
| `..._20260907_094053` → #21 | 495 | 1 de 8 | 7 | 21.886 |
| `..._20260908_093847` | 514 | 4 de 8 | 4 | no guardada |
| `..._20260908_095547` | 514 | 4 de 8 | 4 | no guardada |
| `..._20260908_122006` → #25 | 514 | 4 de 8 | 4 | **7.959** |

No fue el pool: creció de 495 a 514 elegibles, y contando directamente en la
memoria hay 826 sets con Final Tick y Final Tick 6M aceptados sin cuarentena,
748 de ellos en los grupos permitidos (Forex/Indices/Metals) repartidos en 13
símbolos, de sobra para las 13 estrategias que admite `max_sets_per_symbol: 1`.
Tampoco fueron las cuatro exclusiones por OHLC de ese día: a esa escala son
ruido, y el #12 (23-08, 4 sets) ya había hecho 8.911 semanas antes.

El neto es función de la amplitud, no del azar. Con el mismo presupuesto de DD,
el neto por punto de valle sale 89,2 con 8 sets (#17), 81,9 con 7 (#21), 51,4 y
48,3 con 6 (#24, #14), 41,4 con 5 (#13) y 34,6 y 30,5 con 4 (#12, #25). Es la
diversificación la que compra margen de DD, y la cuota se come precisamente eso:
al medirse como fracción del total reciente, cuanta más amplitud tiene la
composición más miembros caen bajo el mínimo.

El registro de decisiones del #25 lo cierra: diez `add_unit` sobre los mismos
cuatro símbolos hasta agotar el valle en 261,40 de 270. El optimizador final
nunca vio otro candidato («no encontro mejora valida tras 16 intento(s), pool 4
candidato(s)»).

## Corrección aplicada a la ruta estándar (2026-09-08)

`_optimize_without_recent_fillers` recibe `refill_from_pool`, apagado por
defecto. Encendido, cada vuelta conserva **todo el pool menos los rellenos ya
descartados** en lugar de solo los supervivientes, así que el DD liberado se
puede volver a gastar y la amplitud sobrevive a la regla.

- Lo enciende `_locked_full_proposals` **solo cuando `experimental_full_search`
  está apagado**. Con el motor encendido el callback es el torneo completo:
  reabrir el pool ahí es el bucle de doce horas de
  `ubs_generation_repeated_tournaments.md`, y además el motor ya repone dentro,
  donde conoce el lote ganador.
- `_monthly_proposals` no pasa el parámetro: el mensual congelado conserva el
  refinamiento por supervivientes, bit a bit.
- Acotado por `STANDARD_ANTIFILLER_REFILL_PASSES = 8`. Agotado el presupuesto
  con rellenos todavía dentro, termina el refinamiento por supervivientes de
  siempre: el resultado entregado nunca sale más sucio, ni más lento de esas
  ocho pasadas, que sin la opción. No se rechaza la generación —es la ruta por
  defecto— a diferencia del motor experimental.
- El aviso guardado distingue las dos rutas: «eliminada(s) **y repuestas desde
  el pool** antes de fijar la composicion A/M/C».

Verificación: 478 pruebas de `python -m unittest discover -s tests` en verde.
Tres regresiones nuevas en `tests/test_portfolio_service.py`, las dos primeras
validadas por mutación:

| Prueba | Mutación que la hace fallar |
| --- | --- |
| `test_recent_fillers_are_replaced_from_the_pool_when_refill_is_on` | filtrar por `active_ids` en la rama de reposición → el segundo pool es `['core']` y `spare` no entra |
| `test_the_standard_bundle_reopens_the_pool_and_the_experimental_one_does_not` | `refill_base = False` → mismo síntoma del #25 |
| `test_refill_is_bounded_and_the_shrink_refinement_still_closes_it` | — acota a 8 reposiciones + cierre por supervivientes, y exige cero rellenos |

Las dos pruebas experimentales que doblaban la primitiva (`run_once`,
`refine_over_survivors`) aceptan ahora el parámetro y **afirman que llega en
`False`**: la ruta experimental no puede acabar reabriendo el pool por descuido.

Nada que portar al fork del nodo: buscando por el texto del mensaje
(`antirrelleno`, `aporte mínimo`, `reabrir el pool`) en la copia ICTrading solo
aparece `requirements.md`, y `tests.test_node_runtime_fork_parity` sigue en
verde (17 pruebas). Este cálculo lo ejecuta el manager Docker, así que
**el cambio no surte efecto hasta reconstruir la imagen y reiniciar el
manager**; editar el fichero no altera una generación en curso.
