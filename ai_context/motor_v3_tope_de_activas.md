# Motor v3: el tope de estrategias activas no ataba a la búsqueda local

## El síntoma

El 2026-11-08, una mejora en cadena del portafolio #113 de AXI (modo Agresivo,
8 originales, «solo estrategias de portafolios activos») murió con:

```
No se encontró una mejora válida con al menos 2 estrategias nuevas (límite de
búsqueda: 5). No se rebaja el mínimo. Intentos: 2: El selector añadió 0
estrategias; este intento requiere 2; 3: ...; 4: ...; 5: ...
```

El mensaje es aritméticamente correcto, pero no dice **por qué** no entró
ninguna candidata, y el motor sí lo sabía.

## Lo que estaba mal

`_select_composition` pide una composición de tamaño exacto: fija
`minimum_active_strategies = maximum_active_strategies = originales + N` y
descarta el intento si el resultado no coincide. El greedy respeta las dos
cotas (`_slot_allows_increment`), pero **`greedy_swap.py` no mencionaba
`maximum_active_strategies` ni una vez**: `_swap_respects_caps` sólo comprobaba
el mínimo. Un intercambio mueve una unidad a otra estrategia y, si esa estaba a
cero, estrena activa. Así que la búsqueda local y el multi-start podían
devolver una cartera **mayor** que la pedida, y el llamante la tiraba entera.

Medido sobre #113 con los informes reales del nodo, intento de 2
incorporaciones, mínimo = máximo = 10 activas:

| pasada | activas | incorporaciones |
| --- | --- | --- |
| sólo greedy | 10 | 2 (válido) |
| greedy + búsqueda local | 12 | 4 (descartado) |
| pipeline completo | 12 | 4 (descartado) |

`greedy_deep.py` sí comprobaba el máximo, pero la selección de la mejora apaga
el refinamiento profundo (`with_deep_refinement(False)`), así que no tapaba el
agujero.

Segundo síntoma, en generación normal: la composición bloqueada A/M/C fija
`maximum_active_strategies=locked_count` y después exige
`active == set(composition.ids)`; una activa de más por la búsqueda local tira
la variante con «no mantuvo todos los sets comunes».

## Lo que hace el motor v3

`SearchPlan.enforce_maximum_in_local_search`, apagado por defecto. Encendido,
`_PassContext.local_search_maximum` lleva el tope a `improve_with_local_search`
y a `improve_with_multi_start_search`, y `_swap_respects_caps` rechaza el
intercambio que pase del máximo.

Llega desde la pantalla como el ajuste booleano `engine_v3`, que
`_optimizer_kwargs` traduce al plan de búsqueda; por eso alcanza a todos los
caminos que fijan un tope sin tocar ninguno de ellos. El diálogo de mejora
manda además `improvement_engine_v3`, que decide sobre lo heredado: una base
guardada antes del motor v3 no trae la clave, y sin ese override no habría
forma de mejorarla con el tope duro.

Queda escrito en la auditoría del portafolio guardado
(`seasonal_validation.portfolio_improvement.engine_v3`), que es lo único que
permite saber después con qué motor se calculó una cartera.

### Qué no cambia

**Ningún portafolio guardado se reescribe.** `_recalculate_saved` conserva las
unidades y sólo llama a `evaluate_portfolio`; nunca reoptimiza. Lo que cambia
es lo que se produzca de ahora en adelante, y sólo donde alguien fija un tope:

| camino | fija tope | corre búsqueda local | le afecta |
| --- | --- | --- | --- |
| Generación normal | no | sí | no |
| Composición bloqueada A/M/C | sí | sí | **sí** |
| Mejora UBS, base y cadena | sí | sí | **sí** |
| Mejora mensual | sí | sí | **sí** |
| Completar y completar mensual | sí | no (`preserve_required_allocations`) | no |
| Recalcular guardado | — | no optimiza | no |

## El motivo, ya en el error

El greedy construye `stop_reason` con el culpable y su recuento («No valid
+0.01 increment: DD limits (10); correlation limits (2)») y hasta ahora el
mensaje de la mejora lo tiraba. Ahora `stop_reason_text`
(`portfolio_improvement_common.py`, compartido por los dos motores) lo traduce
y lo añade al error, que es lo que acaba en el log del trabajo. Es el mismo
diccionario que `friendlyReason` aplica en la pantalla a los portafolios ya
guardados: **si se amplía `_greedy_stop_reason`, hay que ampliar los dos**.

## Cómo se reprodujo

El trabajo fallido no guarda sus `settings`: el estado conserva el error, el
`portfolio_id` y los miembros previos, pero no la petición, y el `settings` que
devuelve `/api/nodes/<id>/portfolio-manager` es el del formulario, que ya no
coincide. Hubo que reconstruirlo: log del trabajo en
`<proyecto>/portfolio_logs/manager_full_history_improve_*.log`, los seis usos
en real de `/api/nodes/<id>/live-audit-config` para rehacer el pool de
«portafolios activos», y `chain._attempt_context` + `chain._selection_kwargs`
contra `PortfolioSource` del nodo para volver a lanzar la selección.

## Lo que queda abierto

- El trabajo sigue sin persistir la petición con la que se lanzó, así que un
  fallo de mejora no se puede reproducir después sin reconstruirlo a mano.
- `_improvement_rank` devuelve tuplas de dos o de cuatro elementos según si la
  comparativa de estrés se completó, y se comparan entre sí: un intento sin
  estrés puede ganarle a uno con estrés bueno porque su primer elemento es la
  ganancia y no un 0/1.
