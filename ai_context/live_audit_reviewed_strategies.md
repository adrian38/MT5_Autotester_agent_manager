# «Validada»: sacar de la portada lo que ya se miró (2026-10-19)

`Portafolios activos` repite auditoría tras auditoría las mismas estrategias
marcadas `AVISO` o `NO REPRODUCE`, también las que el usuario ya abrió, analizó
y decidió dejar como están. Cuando una lista de avisos no se puede vaciar deja
de leerse: lo nuevo no se distingue de lo de siempre.

La casilla **Validada** de la tabla `Demo contra real` de Análisis es la
respuesta. Marca una fila como ya revisada y esa fila deja de contar en la
portada. **No toca el veredicto**: Análisis la sigue enseñando con su color, su
porcentaje y su badge, porque lo que el usuario validó es que ya la miró, no
que reproduzca.

## Validar no es silenciar para siempre

La marca guarda el **veredicto que la fila tenía al validarla**, no un `true`.
La portada la esconde mientras el veredicto actual no sea más grave, con el
mismo `SEVERITY` que ya ordena la tabla:

- validada en `AVISO` y sigue en `AVISO`, o mejora → fuera de la portada;
- validada en `AVISO` y la auditoría siguiente la pone en `NO REPRODUCE` →
  vuelve, y la fila de Análisis lo dice («la validaste con AVISO y ha
  empeorado»).

Sin el veredicto guardado, una estrategia validada un martes podría romperse el
jueves sin que nada lo contara, y la portada es justo la pantalla que existe
para contarlo. Que mejore sí la mantiene fuera: ya se miró.

La portada no se limita a esconder: la tarjeta dice cuántas hay validadas. Un
`SIN AVISOS` que además esconde tres filas afirma más de lo que sabe.

## Dónde vive

En `reviewed_strategies` del perfil del uso, al lado de `view_lot_overrides` y
por el mismo motivo: en `localStorage` la misma auditoría saldría limpia en el
escritorio y llena de avisos en el móvil (`live_dashboard_uses_analysis.md`).

La ruta es la misma puerta que el escalado de la vista: `_handle_view_field` de
`manager_live_audit_routes.py` enruta por tabla los dos campos que Análisis
edita por su cuenta, y el nombre del campo es también el del par de métodos del
almacén (`reviewed_strategies` / `set_reviewed_strategies`). Los dos salen del
payload que se manda al nodo: son criterio del manager.

El esquema **valida el veredicto** contra la misma lista que `SEVERITY`. Un
cliente que grabara una etiqueta desconocida dejaría a la portada decidiendo
«no ha empeorado» sobre algo que no sabe ordenar, o sea escondiendo la fila
para siempre.

## La serie tiene su propia casilla (2026-10-26)

La tabla `¿Y en todas las auditorías que llevamos?` lleva la misma columna
`Validada`, con el mismo contrato: no toca el veredicto acumulado ni el orden
de la tabla, sólo saca la fila de `Portafolios activos`, y vuelve en cuanto el
veredicto acumulado empeora.

**Es otro campo del perfil**, `reviewed_history_strategies`, y eso es lo
importante. Las dos tablas contestan preguntas distintas —«¿qué pasó esta
semana?» y «¿y en todas las que llevamos?»—, así que una marca no puede valer
por la otra: con un solo campo, las doce filas marcadas de la serie del 109 de
AXI se habrían escondido detrás de dos validaciones del periodo. El camino es
el mismo (`_VIEW_FIELDS` de `manager_live_audit_routes.py`, el par
`reviewed_history_strategies` / `set_reviewed_history_strategies` del almacén),
y el validador también: `normalize_reviewed_history_strategies` y
`normalize_reviewed_strategies` comparten cuerpo y sólo cambian el nombre que
sale en el error, que es lo que dice en qué tabla mirar.

La regla de cuándo vuelve a avisar **no se duplica**: la tabla del histórico
llama a la misma `LiveAuditReproduction.reviewed`, y la portada filtra en
`summarizeHistory` con `storedHistoryReviews(profile)`. La sección «todo el
histórico» de la tarjeta ya no desaparece cuando lo único que la vació fueron
las validadas: enseña el recuento, porque esconder tres avisos sin contarlos
afirma más de lo que se sabe.

## Nada que portar a los agentes

Igual que el veredicto y el emparejamiento, esto vive sólo en el manager
(`live_audit_reproduction_verdict.md`). El nodo no recibe el campo y no cambia
nada de lo que audita.

**No hay runtime de JavaScript en este equipo**, así que la regla del
`SEVERITY` está portada a Python en
`tests/test_live_audit_reviewed_strategies.py` y el port exige que la expresión
que mide esté literalmente en `live_audit_reproduction.js`: si alguien la
reescribe, la prueba se cae en vez de medir un criterio que ya no existe.
