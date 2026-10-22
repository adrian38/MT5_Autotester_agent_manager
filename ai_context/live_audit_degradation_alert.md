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
| `warning` que cruzó −2 σ (AVISO FUERTE) | fila con su σ y sus operaciones, en ámbar | no |
| `warning` que no cruzó (AVISO) | sólo el recuento «N en vigilancia» | no |
| el resto | nada | no |

**Desde el 2026-10-20 `DEGRADA` ya no es cruzar −2 σ**, sino cruzarlo y
superar además las dos pruebas de `live_audit_degradation_evidence`: el detalle
y los números están en `live_audit_degradation_evidence.md`.

Eso partió en dos la fila de `AVISO`. La que cruza y no se confirma **se
nombra en la tarjeta** —`degradation_crossing` en el estado del cron,
`crossing` en el payload— y deja la tarjeta en ámbar. Dejarla sólo en el
recuento la escondía, y como la sección únicamente se pintaba habiendo alguna
degradada, la portada se quedaba **muda** mientras una estrategia cruzaba
semana tras semana: exactamente lo que este aviso vino a arreglar. Al móvil
sigue sin ir, que es la diferencia entre «vigila esto» y «cambia esto».

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

## Un portafolio manual tambien tiene historico (2026-10-22)

«Contra el historico» contestaba «SIN HISTORICO» en las 27 filas de
`Christmas_Axi` (AXI, portafolio 109). El rotulo decia la verdad a medias: no
hay backtest **de ese candidato en la memoria UBS**, porque un portafolio
manual no sale del pool —sus miembros son `manual:N`, con `candidate_id` nulo
en `portfolio_members`— y el scope `manual` no es ninguna memoria.

Pero el historico existe, y lo tenian **las 32** estrategias: lo genera el
boton «Generar historico OHLC» de la pantalla del portafolio manual, que deja
el informe en `<proyecto>/reports/` con el nombre del set. La misma regla que
`manual_history_summary` ya usaba para el resumen de ese portafolio.

`read_histories` recurre ahora a ese informe **solo para lo que la memoria no
contesto**: un candidato del pool se sigue midiendo contra lo que la memoria
guarda de el. La regla de emparejado vive en una sola funcion,
`portfolio_manual_reports.report_for_set`, porque dos reglas serian dos
respuestas a la misma pregunta.

Lo que la memoria guarda en `metrics_json` se saca de las operaciones del
informe, que es de donde salio alli: `trades`, `net_profit`, `avg_trade`, el
SQN (`media / desviacion * raiz(n)`), los meses activos del periodo y el valle
de la curva **en orden de cierre** —el orden del informe da otro valle—. Se
conserva el `report_path`, que es lo que permite el contraste contra tramos
consecutivos del propio backtest (`live_audit_degradation_evidence`).

Medido sobre `Christmas_Axi`: de 0 a **32 de 32** con historico, y 25 de esos
32 drawdowns caen a menos del 5 % del `dd_001` que el usuario tenia apuntado a
mano en `riesgo_xmas.xlsx` —una corroboracion independiente de que los
informes leidos son los suyos—. Los siete que difieren son modestos y el
numero del Excel no tiene por que ser el mismo: aqui se mide el valle de
balance cerrado, no el flotante.

**La leccion.** El rotulo era literalmente cierto y por eso lo di por bueno:
«no hay backtest de este candidato en la memoria del agente». Lo que no decia
es que ese no es el unico sitio donde un portafolio puede tener historico. Un
mensaje exacto sobre donde se ha mirado no es un mensaje exacto sobre lo que
existe.
