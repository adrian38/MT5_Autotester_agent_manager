# La degradación sale de Análisis a la tarjeta y al móvil (2026-10-20)

Petición del usuario: «si una estrategia se degrada en análisis tiene que salir
notificación tanto en la tarjeta como push».

La tabla **«contra el histórico»** de Análisis ya decidía que una estrategia
`DEGRADA` —`live_audit_degradation.compare`, contraste de medias contra el
backtest de la memoria UBS—, y es el único veredicto que justifica retirarla:
`NO REPRODUCE` dice que la cuenta real no siguió al tester, esto dice que la
estrategia ya no es la que se eligió. Pero había que abrir esa pantalla, uso por
uso, para enterarse. La portada contaba reproducción y la campana contaba
auditorías; nadie contaba esto.

## Qué sale y qué no

| Veredicto | Tarjeta | Campana y push |
| --- | --- | --- |
| `degraded` (DEGRADA) | fila con su σ y sus operaciones | sí, una vez por estrategia |
| `warning` (AVISO) | sólo el recuento «N en vigilancia» | no |
| el resto | nada | no |

**Desde el 2026-10-20 `DEGRADA` ya no es cruzar −2 σ**, sino cruzarlo y
superar además las dos pruebas de `live_audit_degradation_evidence`: el detalle
y los números están en `live_audit_degradation_evidence.md`. Lo que cruza y no
las supera se queda en `AVISO` —la pantalla lo llama `AVISO FUERTE`— y por lo
tanto **no** sale a la campana ni al móvil. Esta tabla no cambia; cambia qué
filas entran en la primera fila de ella.

`AVISO` es, por el propio criterio, «mantener en vigilancia; aún no hay base
suficiente para cambiarla». Llevarlo al móvil sería un aviso semanal sobre el
que no se puede hacer nada, y eso vacía de sentido a los que sí importan.

**Una estrategia se anuncia una sola vez** mientras siga degradando. Si sale de
`DEGRADA` y vuelve a entrar, vuelve a avisar. Es la lección de
`live_audit_reviewed_strategies.md`: una lista de avisos que no se puede vaciar
deja de leerse.

A diferencia de `record_transitions`, **la primera vuelta sí habla**. Aquella
calla porque «la auditoría acaba de terminar» es una noticia y anunciarla tres
semanas tarde sería falso; «esta estrategia degrada» es una afirmación sobre el
estado de hoy, y callarla el primer día escondería justo lo que se pidió
enseñar.

## Se mide sobre el archivo, no sobre el nodo

`live_audit_degradation_alerts.review` no le pide nada al agente salvo su
memoria UBS. Lee la **última ejecución archivada** del uso
(`live_audit_archive`) y acumula la serie con
`manager_live_audit_reports.accumulated_degradation`, que es la misma primitiva
que sirve la pantalla: dos cálculos serían dos criterios y la campana acabaría
avisando de algo que Análisis no enseña.

Tres consecuencias:

- No hay que reanalizar la materia prima del nodo: `archive_execution` acaba de
  hacer ese trabajo en la misma vuelta, y el vigilante corre justo después.
- **La primera vuelta tras reiniciar el manager rescata sola** la degradación de
  lo ya archivado, sin esperar a la cita siguiente. El candado es el
  `execution_id` guardado en `degradation_execution`, igual que
  `archive_issue`.
- Lo acumulado es lo que manda, no la semana. En las pruebas se ve: una
  estrategia con tres operaciones a favor en una auditoría y doce en contra en
  la siguiente degrada por el conjunto, no por la última.

## Por qué el resultado se guarda en el estado del cron

La tarjeta de «Portafolios activos» se repinta **cada 60 segundos** y el
informe abre la memoria UBS del agente. Calcularlo en cada sondeo sería abrir
esa base sesenta veces por hora y por uso, que es exactamente lo que la portada
evita desde `live_dashboard_uses_analysis.md` y lo que ya se resolvió igual para
el alias del portafolio.

Así que `review` deja en el registro `nodo/uso` de
`runtime/live_audit_schedule_state.json` las filas degradadas, el recuento de
las que están en vigilancia, cuántas se compararon y cuándo; y
`dashboard_node_state` lo copia al payload de la portada. La tarjeta sólo pinta.

Perder ese fichero no relanza nada: se pierde la marca y la degradación se
vuelve a medir y a anunciar en la vuelta siguiente. Lo que sigue vigente es el
aviso de `live_audit_cron_and_bell.md`: **el suite no puede escribir en
`runtime/`**, o reaparecen avisos de nodos inventados.

## Lo que no se pudo medir no se da por bueno

Si la memoria del agente no se deja abrir, `accumulated_degradation` levanta y
`review` lo anota en el log **sin** marcar la ejecución como medida: la vuelta
siguiente reintenta. Grabar «no degrada nada» sobre lo que no se pudo leer sería
la versión silenciosa del grafo congelado.

El caso contrario sí se da por bueno: `read_histories` contesta «sin histórico»
para un candidato que la memoria no tiene, y eso es un hecho, no un fallo.

## Nada que portar a los agentes

Como el veredicto, el emparejamiento manual y las estrategias validadas, esto
vive sólo en el manager. El nodo no recibe nada nuevo y no cambia nada de lo que
audita. El usuario sí tiene que **reconstruir la imagen del manager**, que corre
en Docker.
