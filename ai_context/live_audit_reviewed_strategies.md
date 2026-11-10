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

### La etiqueta no basta (2026-10-26)

Comparar sólo la etiqueta escondía un deterioro entero: un `AVISO` validado con
el 24 % de déficit podía irse al 55 % —a un punto de `NO REPRODUCE`— y seguir
fuera de la portada y con la casilla marcada, porque la palabra no había
cambiado. Así que la marca guarda también el **déficit** (`pct`) y empeorar
dentro de la misma etiqueta retira la validación igual que cambiar de etiqueta.

El margen es `SAME_VERDICT_MARGIN_PCT = 5` puntos, y no es cero a propósito: el
déficit se recalcula en cada auditoría y se mueve décimas sin que la estrategia
haya cambiado, así que con tolerancia cero la primera re-ejecución retiraría
validaciones que nadie ha empeorado.

`pct` es **opcional** en el esquema. Las marcas guardadas antes de que el campo
existiera no lo llevan, y ésas se siguen comparando sólo por veredicto en vez
de desaparecer o quedarse pegadas para siempre.

La fila lo dice con números, no con un «ha empeorado» que no se puede
comprobar: `reviewDegradation` añade «del 24 % de déficit al 55 %», y vive en
`live_audit_reproduction.js` porque las dos tablas enseñan el mismo aviso.

Lo que **no** hace: borrar la marca. Sigue guardada con su veredicto y su
déficit, que es lo que permite explicar por qué volvió. Consecuencia conocida:
si una auditoría posterior devuelve la fila a su déficit de antes, se vuelve a
esconder sin que nadie la haya revisado después del deterioro.

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
llama a la misma `LiveAuditReproduction.reviewed` —con el déficit acumulado
(`shortfall_pct`) como cuarto argumento, igual que la del periodo pasa
`numbers.pct`—, y la portada filtra en `summarizeHistory` con
`storedHistoryReviews(profile)`. La sección «todo el
histórico» de la tarjeta ya no desaparece cuando lo único que la vació fueron
las validadas: enseña el recuento, porque esconder tres avisos sin contarlos
afirma más de lo que se sabe.

## Y «contra el histórico» tiene la suya (2026-11-02)

Esa tabla no tenía casilla. Sus alarmas —`DEGRADA`, `AVISO FUERTE` y «opera
menos»— salían en `Portafolios activos` y en el resumen del mes auditoría tras
auditoría sin que nada pudiera vaciarlas: el camino
`live_audit_degradation_alerts.stored` → tarjeta no miraba ningún campo
validado. Medido el 2026-11-02 sobre el informe de octubre, **5 de las 12
alarmas eran de estrategias que el usuario ya había validado** en las otras dos
tablas; la del 105 de AXI estaba marcada en las dos desde el 25 y el 26 de
octubre y la tarjeta seguía diciendo «1 estrategia en el límite».

**Tercera marca y no una de las dos anteriores.** Aquéllas dicen que el real se
parece al tester —esta semana, o en toda la serie— y ésta que la estrategia
sigue haciendo lo de siempre. Una puede reproducir a la perfección y estar
degradándose, así que dejar que una validación de reproducción callara esta
alarma escondería justo la que nadie ha mirado. El campo es
`reviewed_degradation_strategies` y el camino es el de siempre: `_VIEW_FIELDS`
de `manager_live_audit_routes.py` y el par del almacén.

Dos ejes, porque la fila enseña dos hechos distintos:

- **El veredicto**, ordenado en `live_audit_degradation_reviews.SEVERITY`:
  `warning` < `crossed` < `degraded`. `crossed` es el `AVISO FUERTE` de la
  pantalla, que no es un veredicto del servidor sino un `warning` cuya
  evidencia cruzó: sin él, validar un aviso taparía el cruce de la semana
  siguiente.
- **`slow`**, que no es un veredicto peor sino otra cosa. Validar el veredicto
  no calla «opera menos», y al revés.

Empeorar sin cambiar de nivel también retira la marca, como en reproducción,
pero la magnitud son **sigmas** y no un déficit: más negativo es peor y el
margen es `SAME_LEVEL_MARGIN_SIGMA = 0.5`.

### Se esconde al leer, no al medir

`live_audit_degradation_alerts.review` deja lo medido en el estado del cron con
un candado de `execution_id@CRITERION`, así que filtrar ahí dejaría la
validación sin efecto **hasta la auditoría siguiente**: una semana mirando una
alarma ya mirada. Por eso filtra `stored()`, que es el único punto del que leen
las dos pantallas que la enseñan.

### El criterio no vuelve al navegador

Al revés que la regla de reproducción, ésta vive en Python y la pantalla no la
recalcula: `manager_live_audit_reports.accumulated_degradation` anota cada fila
con `reviewed`, `review`, `review_level` y `review_slow`, y
`live_audit_analysis.js` sólo pinta la casilla y guarda el nivel que le
mandaron. Marcar y desmarcar no vuelve a pedir el informe —abre la memoria UBS
del agente— porque la marca se construye con los valores de la propia fila y
por construcción la tapa; quien decide en la carga siguiente sigue siendo el
servidor.

La portada y la tarjeta del mes cuentan lo escondido (`reviewed`): un «sin
avisos» que además calla tres filas afirma más de lo que sabe. Y por eso
`monthly_summary_notices.CRITERION_VERSION` subió a
`seasonal-bands-profit-and-drawdown-v6`.

## Nada que portar a los agentes

Igual que el veredicto y el emparejamiento, esto vive sólo en el manager
(`live_audit_reproduction_verdict.md`). El nodo no recibe el campo y no cambia
nada de lo que audita.

**No hay runtime de JavaScript en este equipo**, así que la regla del
`SEVERITY` está portada a Python en
`tests/live_audit/test_live_audit_reviewed_strategies.py` y el port exige que la expresión
que mide esté literalmente en `live_audit_reproduction.js`: si alguien la
reescribe, la prueba se cae en vez de medir un criterio que ya no existe.
