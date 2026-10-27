# La portada reutiliza el veredicto de Análisis

## Error observado (2026-10-14)

La portada y la tabla `Demo contra real` calculaban reproducción en dos
implementaciones distintas. La tabla permite simular lotes y guarda esa vista
en `localStorage`; la portada ignoraba esa configuración y podía mostrar
`NO REPRODUCE` donde Análisis mostraba `REPRODUCE`.

Dos intentos de corregirlo añadieron reglas exclusivas de la portada
(`ATRIBUCIÓN PENDIENTE` y normalización automática al lote esperado). Ambos
eran conceptualmente incorrectos: `NO REPRODUCE` y `AVISO` siguen siendo
veredictos válidos con comparación parcial, y la simulación del usuario no debe
convertirse silenciosamente en el criterio permanente de la auditoría.

## Contrato

`live_audit_reproduction.js` es la única primitiva para:

- aplicar la simulación manual o `Escalar demo al lote real`;
- sumar Demo y Real por estrategia, incluidas las reales atribuidas;
- calcular el déficit y resolver `REPRODUCE`, `AVISO`, `NO REPRODUCE` o
  `MEJOR QUE EL TESTER`.

Análisis y la portada cargan esa misma primitiva. La portada lee el escalado
que el usuario dejó aplicado en Análisis; sin nada aplicado muestra el veredicto
bruto, igual que la tabla al pulsar `Restablecer`.

**Dónde se lee cambió el 2026-10-17.** Estaba en `live-audit-lots:<nodo>:<uso>`
del mismo navegador, así que la misma auditoría salía reconciliada en el
escritorio y en bruto en el móvil. Ahora es `view_lot_overrides` del perfil del
uso, que ya viaja en el payload de la portada
(`ai_context/live_account_audit_mvp.md`). Lo que sigue en pie es lo de arriba:
la portada no normaliza por su cuenta: aplica lo que el usuario decidió, o nada.

Las salvedades (`POCAS OPERACIONES`, comparación parcial y calidad tick) siguen
viviendo en Análisis; no sustituyen el veredicto.

## La caché de la tarjeta sigue la revisión de Análisis (2026-10-26)

La ejecución puede conservar el mismo `audit_id`, `completed_at` y
`analysis.executed_at` mientras el manager corrige sus parejas —por ejemplo al
recuperar cierres del informe nativo—. La tarjeta usaba esa fecha como sello de
su caché histórica: Análisis ya mostraba AUDJPY 24,03 €, XAGUSD 10,97 € y US500
−3,52 €, pero la tarjeta retenía la copia anterior y pintaba `Real 0,00 €`.

El endpoint de la portada ya analiza una vez cada uso con el criterio vigente.
Ahora archiva **ese mismo resultado ya analizado** y entrega una huella de su
`comparison_detail`; no vuelve a calcular otro. El navegador usa esa huella
para invalidar sólo la serie que cambió. Si la huella coincide, el archivo no
se reescribe y la tabla pesada tampoco se vuelve a pedir.

El aviso de carga de esa serie dura hasta que termina la petición. Antes se
añadía el billete a `historiesTried` justo antes del `await`; si otro nodo
repintaba la portada durante el análisis, la tarjeta interpretaba «ya se
intentó» como «ya terminó» y quitaba el aviso aunque Análisis siguiera
trabajando. `historiesLoading` conserva ahora el estado en curso y
`historiesTried` sólo se actualiza después de que la petición se resuelva, con
éxito o con error.

## Reinicio desde la portada

El botón inicial enviaba el `POST` pero no sondeaba el estado. Quedaba bloqueado
y no recargaba la página, aunque el trabajador hubiese reconstruido el manager.
Ahora sigue `GET /api/manager/restart`, tolera el corte de conexión durante el
reemplazo, recarga al completar y abre Generación si GitHub necesita
autorización, donde ya existe el diálogo con el código de dispositivo.

Todo esto vive en el manager; no requiere port a los runtimes de los agentes.

## La portada no sondea el estado general (2026-10-15)

La carga inicial ya no encadena `/api/nodes`, la configuración de cada nodo y
un detalle por uso. `/api/live-dashboard` devuelve sólo las identidades que el
manager ya tiene en memoria y cada `/api/nodes/<id>/live-dashboard` hace una
única lectura de `/api/v1/live-audits`, analiza de ahí los usos marcados como
reales y elimina la materia prima antes de responder al navegador.

La única excepción es el alias del portafolio, que vive en la memoria UBS del
agente: se pide por su propia ruta, una vez por nodo y no en cada sondeo
(`ai_context/portfolio_alias.md`).

Las peticiones por nodo son independientes y la portada repinta cada vez que
llega una: un agente lento ya no deja vacías las tarjetas de los demás. La ruta
general `/api/nodes` conserva su contrato para Generación, pero no participa en
la portada. Esta mejora vive sólo en el manager y es compatible con el protocolo
actual de los agentes; una futura ruta resumida en el runtime podría reducir aún
más los bytes de la única lectura restante.

## Los cuatro importes son acumulativos (2026-10-27)

`Profit Test`, `Profit Real`, `DD Test` y `DD Real` de la portada describen
ahora toda la serie archivada, no sólo la última auditoría. La fuente sigue
siendo el cálculo único de Análisis: `LiveAuditHistoryVerdicts.summary` resuelve
una vez el recorte de periodos solapados, los grupos, los veredictos y las
cuatro métricas; tanto la tabla histórica como la tarjeta consumen ese mismo
resultado. `dashboard_metrics.js` no vuelve a acumular ni a deduplicar.

Mientras la serie todavía no ha llegado, esos importes no se pintan: la tarjeta
muestra el aviso de carga que ya identifica «la serie archivada». Tampoco se
recurre a los importes de la última auditoría si la lectura falla, porque se
presentarían como acumulados sin serlo.
