# El resumen del mes cerrado

Lo que se mira el día 1 para decidir si una cartera sigue, se vigila o se
cambia. Un cron lo calcula solo, lo guarda y deja un aviso en la campana; el
botón «Resumen del mes» de la portada de cuentas y de «Portafolios activos»
abre **su propia ventana**, `/monthly_summary.html`.

Los módulos, en orden de dependencia:

`monthly_summary_schedule` → `monthly_summary_coverage` →
`monthly_summary_evidence` → `monthly_summary_notices` →
`monthly_summary_store` → `monthly_summary_build` → `monthly_summary_watch` →
`monthly_summary_runtime` → `manager_summary_routes`

Y la pantalla: `static/monthly_summary.html` con `static/monthly_summary.js` y
`static/monthly_summary.css`; el botón que la abre es
`static/monthly_summary_button.js`, que es otro fichero a propósito: la
portada no tiene por qué cargar el pintado que nunca usa, ni los dos ficheros
del veredicto de reproducción que éste arrastra.

## Lo que mide, y por qué estas cosas

- **El mes cerrado contra lo que ese mes prometía el backtest**, dicho
  **contando meses de la propia serie** («de los 6 octubres, 4 fueron peores»)
  en vez de suponer una normal. Es el método que `live_audit_degradation_evidence`
  ya usa con el beneficio por operación, por la misma razón: la distribución
  mensual tiene la cola izquierda gorda y rachas.
- **Y se cuenta contra los meses del mismo mes del año, no contra los setenta
  y siete.** La promesa de la tarjeta ya es estacional —`month_expectation`
  promedia los octubres del backtest—, así que situar octubre entre los setenta
  y siete meses lo compara con una población distinta de la que produjo esa
  promesa: «de los 77 meses, 53 fueron peores» mezcla eneros con agostos y no
  dice nada de si este octubre fue bueno. El recuento estacional usa el mismo
  listón de muestra que el general (`MINIMUM_MONTHS`), así que con menos de
  seis años de backtest dice que no puede situarlo en vez de inventar un
  porcentaje sobre tres datos. `overall` se sigue calculando, pero no se
  enseña. La petición es del usuario, 2026-11-02.
- **El drawdown del mes contra los drawdowns mensuales del backtest.** Era lo
  que la última sección de `live_audit_degradation_evidence.md` daba por no
  medido en absoluto, y es la familia de criterios con la que de verdad se
  retira una cartera. `beyond` es más hondo que el peor mes que hizo nunca;
  `extreme`, por encima del 95 % sin llegar al peor. **Éste sí cuenta contra
  todos los meses**, al revés que el beneficio, y por dos razones: no tiene
  promesa estacional detrás con la que tenga que casar, y seis octubres son
  seis datos con los que no se retira una cartera —además de que el cuantil
  del 95 % sobre seis valores es el máximo, así que el escalón de VIGILAR
  desaparecería y todo sería «inédito» o nada. Lo que no puede quedarse
  implícito es la base, así que el aviso dice cuántos meses contó.
- **La racha de meses por debajo**, que es lo que convierte un mes flojo en una
  decisión. Un mes sin promesa **corta** la racha en vez de saltarse: una racha
  con un agujero no es una racha, es dos trozos.
- **Las estrategias que degradan**, traídas de donde el vigilante ya las dejó
  archivadas. No se vuelven a medir: sería un segundo criterio para el mismo
  veredicto.
- **Sólo lo que está al mismo lote.** La serie de meses del backtest se guarda
  a los lotes declarados y no se remide: `months_at` trae **la fila del mes
  objetivo** y nada más, porque guardar la tabla estacional entera doce veces
  engordaría el fichero. Con autolote, entonces, la promesa puede estar al lote
  que la cuenta corrió y la serie nunca lo está. Contar «de los 77 meses, N
  fueron peores» o llamar «inédito» a un drawdown contra esa serie compararía
  el tamaño de la posición y no el comportamiento, que es lo que la cabecera de
  `monthly_summary_evidence` dice que no puede pasar. Así que la tarjeta trae
  dos marcas —`promise_comparable` y `table_comparable`— y lo que no es
  comparable **no se enseña como número: se dice por qué**, con
  `notices.AUTOLOT_PROMISE` y `notices.AUTOLOT_TABLE` en el sitio donde iba la
  comparación. Un mes cuya promesa no está a su lote tampoco entra en la racha
  ni la rompe, por lo mismo que un mes sin promesa.
- **La cobertura**, que tapa a todos los demás. Con medio tramo auditable sin
  auditar el veredicto no es MANTENER, es SIN CONCLUSIÓN. Si el portafolio nació
  dentro del mes, la cobertura exigible empieza en su fecha de alta: sí admite
  una conclusión cualificada: VIGILAR hasta tener el primer mes natural
  completo. Beneficio y drawdown se recortan al alta; no se comparan con meses
  completos del backtest, y las rachas nunca atraviesan la fecha de creación.

## Decisiones que no se deducen del código

- **No lanza nada.** Los otros dos crones llevan candado doble porque lanzar
  entra en un agente y le pausa el pipeline; éste sólo lee ficheros del disco
  del manager. El interruptor decide si se calcula solo, no si algo peligroso
  puede pasar, y por eso **nace armado** al revés que aquéllos.
- **La unidad es la cuenta real, no el portafolio.** El beneficio y el drawdown
  del mes son de la cuenta; repartirlos entre dos portafolios que la comparten
  exigiría inventar un reparto. La tarjeta los nombra y les cuelga lo que sí es
  suyo, igual que `live_account_dashboard`.
- **Una foto vieja dice que lo es.** El informe guarda
  `notices.CRITERION_VERSION` y el estado del almacén trae el de hoy; si no
  coinciden, la pantalla avisa en la barra. Hace falta porque los recuadros se
  pintan **en vivo** y los avisos vienen escritos: tras un despliegue la misma
  tarjeta dice dos cosas del mismo número, y eso se ha leído dos veces como un
  fallo del panel —«Comparación histórica: No comparable» encima de un drawdown
  inédito; «Octubres de backtest 6» encima de «de los 77 meses»— cuando lo que
  faltaba era pulsar «Volver a calcular». La versión cubre todo el criterio, el
  de `monthly_summary_build` incluido, así que se sube al tocar cualquiera.
- **Se guarda el resultado, no la receta.** Un resumen es la foto de un mes con
  los datos que había al cerrarlo; recalcularlo en marzo da otra cosa, porque
  el archivo ya tiene auditorías que entonces no estaban. «Volver a calcular»
  existe, avisa de eso y **borra la marca de leído**: lo que el usuario miró ya
  no es lo que hay.
- **Es una ventana, no un diálogo** (petición del usuario, 2026-10-25). El
  resumen se lee con las tarjetas de las cuentas delante y son doce cuentas
  con tres avisos cada una: un modal obligaba a cerrarlo para mirar cualquier
  otra cosa. El aviso de la campana lleva a ella con su mes puesto
  (`?month=2026-09`), para que al abrirlo tres días después desde el móvil
  salga el mes que se anunció y no el último guardado.
- **El aviso del día 1 es también un aviso al móvil, y no por hacer nada**:
  `LiveAuditNotificationStore.add` es la única puerta de la campana y
  anuncia por su cuenta —`manager_server` le enchufa `web_push.announce`—,
  así que publicar no es un paso que este cron tenga que acordarse de dar.
  Lo vigila `tests/manager/test_notice_single_door.py`, que además impide
  que un módulo se fabrique un aviso por su cuenta y que `SKIPPED_KINDS`
  crezca sin que nadie lo decida. Es **un** aviso por resumen y no uno por
  cuenta: el resumen existe justamente para leer las doce de una vez.
- **El veredicto de reproducción no se porta a Python.** Vive en
  `static/live_audit_reproduction.js` y lo comparten Análisis y la portada; una
  segunda copia acabaría diciendo otra cosa. El panel lo pinta con la función
  original, y por eso carga también `dashboard_metrics.js`. Junto con el alias
  del portafolio —que vive en la memoria UBS del agente y va pegado al
  nombre, como en la portada— es lo único del resumen que entra en el nodo,
  así que los dos se piden **después** de pintar las tarjetas: que fallen
  dejan una tarjeta sin esa línea, nunca sin tarjeta.
- **«Dejó de operar» se quedó fuera** (petición del usuario, 2026-10-24). Se
  sigue midiendo y guardando en el estado del cron, pero es una señal rápida
  que ya se ve en la tarjeta de la portada y aquí sólo añadiría una línea más
  que leer cada mes.
- **El día de la cita se limita a 1…28.** Un 31 no existe en febrero, y una
  cita que unos meses no llega es una cita que calla justo cuando toca decidir.
- **«Obtener lo que falta» encadena las dos recuperaciones existentes.** Primero
  lanza el sondeo manual de cuentas y después encola la recuperación de huecos
  de auditoría; espera las dos actividades y recalcula el mes al terminar. Los
  agentes siguen rechazando un arranque si el terminal está ocupado. Lo que no
  se puede reconstruir permanece `SIN CONCLUSIÓN`. Nacer durante el mes ya no
  es uno de esos bloqueos: da VIGILAR sobre el tramo real desde el alta y lo
  marca como parcial; un portafolio que todavía no existía en ese mes sigue sin
  conclusión. El botón obtiene evidencia, no inventa historia.
- **El alias abre la tarjeta.** Es la identidad que el usuario reconoce, así
  que aparece destacado antes que cuenta, cifras y avisos. El nombre guardado
  queda debajo como referencia técnica y al final conserva su enlace al
  análisis. Colores y orden conservan literalmente el contrato de Cuentas en
  real y Portafolios activos: AXI rojo, RoboForex azul, ICTrading verde y
  Vantage violeta; primero se agrupa por broker y las prop quedan al final.
  El veredicto colorea sólo su chapa, nunca sustituye la identidad del broker.
- **Las marcas de lote son tolerantes con lo ya guardado.** `notices.comparable`
  sólo calla una comparación ante un `False` explícito. Un resumen es la foto de
  un mes y se relee tal cual se guardó: los de antes de medir los lotes no traen
  las marcas, y ausente tiene que seguir significando lo que significaba
  entonces, o la foto cambiaría sola al desplegar.
- **«Obtener lo que falta» nace apagado y lo enciende el informe.** Cada tarjeta
  ya dice en `recovery` qué se puede obtener, y `applyRecoveryPlan` lo resuelve
  al pintar. Antes el botón sólo se apagaba mientras corría y el plan se miraba
  **después** de pulsar —recalculando el resumen entero para descubrir que no
  había nada que hacer—, así que pulsarlo con el mes cubierto sondeaba doce
  cuentas y pausaba sus pipelines para no cambiar nada. Al terminar vuelve a
  decidirlo el informe, no el hecho de haber terminado.
- **La barra de actividad es la de siempre, con sus clases.** La recuperación
  tarda minutos y antes sólo dejaba un `<p>` con texto: no había forma de saber
  si estaba haciendo algo. Ahora usa el mismo punto, barra y relleno que
  `cron-activity` —la cita de auditoría de la cabecera de todas las pantallas—,
  en su propia fila justo encima de los botones. El relleno es **trabajo
  terminado** (sondeos y auditorías que ya no están pendientes), nunca tiempo
  pasado: con dos horas de margen, una barra que no significa nada es peor que
  ninguna.
- **La serie queda cerrada con una lectura posterior al fin del mes.** No se
  exige esperar tres días ni volver a sondear hasta coincidir con la hora del
  cálculo: esa comparación móvil hacía obsoleta incluso una captura recién
  terminada. `updated_at >= fin del mes` prueba que el terminal ya pudo leer
  todo el mes; una lectura anterior sigue pidiendo el sondeo del botón.

## Cómo se entregó

En dos commits, y el primero estuvo una semana sin el segundo: el lado del
manager se commiteó el 2026-10-18 en `claude/resumen-mensual` —una rama de
sesión, que `AGENTS.md` prohíbe— y se quedó ahí, con `monthly_summary.js` y
`monthly_summary.css` ya declarados en `STATIC_FILES` pero sin existir. El
botón, el panel y las pruebas llegaron el 2026-10-25, rescatando la rama con
`git cherry-pick` sobre `main` (sin conflictos, 17 commits después).

Lo que enseña, que es por qué está escrito aquí: una entrega «a medias a
propósito» no se nota en verde. El suite pasaba, las rutas contestaban y nada
señalaba las dos rutas estáticas que apuntaban a ficheros inexistentes.

## Dónde se comprueba

| Prueba | Qué exige |
| --- | --- |
| `tests/live_audit/test_monthly_summary.py` | El criterio: racha cortada por el hueco, drawdown inédito, cobertura unida y no sumada, que SIN CONCLUSIÓN tape a CAMBIAR, y que con autolote el mes diga por qué no se sitúa en vez de contar. |
| `tests/manager/test_monthly_summary_screen.py` | Que el panel pinte y no decida, que sus rutas y su almacén se porten, que el enlace de la campana lleve a una página que existe, que «Obtener lo que falta» sólo esté vivo cuando falte y que la barra use las clases de `cron-activity`. |
| `tests/manager/test_header_actions_order.py` | El sitio del botón en la barra de las dos pantallas. |
