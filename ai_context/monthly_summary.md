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

- **Si el mes respeta la media de los suyos**, que es la pregunta que la
  pantalla tiene que contestar. No dónde cae: un recuento es una posición, y
  «de los 6 octubres, 4 fueron peores» no dice si la cartera se está portando
  como lo que se compró. Lo que lo dice es «éste hizo el 138 % de lo que da un
  octubre», o el 45 %.
  - **Lo normal es la media de ese mes del año ± su desviación**, y la banda
    sale de la dispersión de esos mismos meses: cuánto puede variar un octubre
    sin que pase nada lo dicen los octubres, no un umbral a ojo. Regla elegida
    por el usuario el 2026-11-02, después de que las dos versiones anteriores
    —contra los 77 meses y contra los 6 octubres— se quedaran en recuentos.
  - **La media es exactamente la promesa**, porque `month_expectation`
    promedia los mismos años. Por eso la frase ya no repite «Prometía X (−Y de
    diferencia)» y el recuadro se llama «Media de los octubres».
  - **Mismo listón de muestra que el recuento** (`MINIMUM_MONTHS`): con menos
    de seis años no se dice qué es normal en un octubre. Y sin media positiva
    no hay porcentaje —el 45 % de una pérdida media no significa nada—, así
    que se dice con palabras.
  - **Salirse por abajo decide**: `seasonal_shortfall` pone la tarjeta en
    VIGILAR sin esperar a la racha, porque por debajo de *lo normal* no es lo
    mismo que por debajo de la media —la mitad de los meses quedan por debajo
    de la media y no pasa nada—. Calla en cuanto la racha habla, que ya es
    CAMBIAR.
- **El drawdown, con la misma vara y contra sus octubres.** Es la familia de
  criterios con la que de verdad se retira una cartera, y era lo que la última
  sección de `live_audit_degradation_evidence.md` daba por no medido en
  absoluto. Se juzga igual que el beneficio porque el usuario lo pidió así el
  2026-11-02: lo que le cuesta un octubre lo dicen los octubres. `deeper` es
  salirse de la banda —VIGILAR— y `beyond` es más hondo que el peor de esos
  meses —CAMBIAR, porque nunca pasó en uno—.
  - **La banda se recorta en cero.** Un drawdown no es negativo, y con una
    desviación mayor que la media `media − desviación` daría un suelo que no
    existe.
  - **El peor se nombra**: «31,07 EUR, octubre de 2025». Una cifra suelta no
    se puede ir a mirar, y el nombre dice si el listón es reciente o lleva
    cinco años sin acercarse.
  - Esto retiró el escalón del cuantil del 95 %: sobre seis valores era el
    máximo, así que `extreme` sólo saltaba en un empate exacto. La banda da el
    escalón intermedio que aquello prometía y no daba.
- **Y al lado, la media de un mes cualquiera.** Contexto, no criterio: que un
  octubre prometa 711,97 cuando un mes corriente promete 621,27 dice que es de
  los buenos de la cartera, y quedarse en 318,44 pesa distinto que en un mes
  flojo de por sí. Son `monthly_mean.profit` y `monthly_mean.drawdown`, dos
  recuadros que entraron el 2026-11-02 a petición del usuario en el sitio del
  recuento de octubres, que se fue: el número de la muestra ya lo dice la
  frase («media 27,59 EUR, 6 octubres del backtest»).
- **El color es la misma comparación que la frase.** «Beneficio del mes» y
  «Drawdown del mes» salen verdes o rojos contra **la media de su mes del
  año**, no contra cero ni contra la histórica, porque es el juicio que la
  tarjeta está haciendo. En el drawdown está al revés —más hondo es peor—, así
  que lo decide `tone` y no el signo. Sin media no hay color: un verde sin
  nada detrás se lee igual de fuerte que uno medido.
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
- **Los siete recuadros son los mismos en todas las tarjetas** (2026-11-02).
  Antes la tarjeta que no podía comparar cambiaba cuatro por el motivo
  —«Promesa comparable: No disponible», «Comparación histórica: No
  comparable», «Meses completos de referencia»— y dos tarjetas de la misma
  pantalla no se dejaban leer en paralelo: había que releer los títulos en
  cada una. Ahora el motivo vive en la frase de debajo, que es donde cabe, y
  el recuadro sin medida dice «sin medir». Lo exige
  `test_every_card_shows_the_same_seven_boxes`.
- **Un tramo tiene objetivo, y es el del resumen del portafolio** (2026-11-02).
  `live_account_monthly.add_month_expectations` ya prorratea la media del mes
  por los días vividos y juzga el beneficio contra eso; el drawdown no se
  prorratea, porque un valle no se reparte por días. `_tranche` repite esa
  cuenta —no una parecida— para que las dos pantallas no den dos objetivos del
  mismo tramo, y las medias de referencia viajan al mismo tramo: un número a
  mes entero al lado de uno de diecisiete días compara dos cosas distintas con
  la misma cara. Sigue sin decidir: el veredicto de un tramo es VIGILAR.
- **Con autolote sí se sitúa el mes entre los suyos, desde el escenario**
  (2026-11-02). La objeción era el lote, no el autolote: `months_at` guarda
  ahora los meses de ese mes del año remedidos a los lotes que la cuenta
  operó, más la media de un mes cualquiera en tres números, así que la banda
  está al mismo tamaño que el mes que juzga. `table_comparable` deja de ser un
  no incondicional. Todos los portafolios de la cuenta o ninguno: media cuenta
  remedida no es una cuenta remedida.
- **Encender el autolote caduca la promesa guardada** (2026-11-02). Diagnóstico
  del `Arreglo del portafolio #148`, que salió en el resumen de octubre sin
  `months_at` ninguno pese a tener autolote activo, 19 reglas enlazadas y
  balance de los dos meses: se descartó una a una cada salida temprana de
  `live_account_ingest._scenarios` —modo, `rules_by_strategy` (19 de 19),
  balance, `month_lots` (19 lotes)— y ninguna disparaba. La causa estaba antes:
  `_needs_rebuild` no llamaba siquiera a `_rebuild`. Encender el autolote no
  cambia el criterio, ni el modo —que es la casilla de la simulación de lotes,
  otra cosa—, y la regla del mes mira `months_at and mes not in months_at`, que
  con la tabla vacía no dice nada. Quedaba sólo el reloj de
  `EXPECTATION_MAX_AGE_HOURS`: la promesa se escribió a las 14:56 del 1, el
  autolote llegó con la auditoría a las 15:01 y el resumen se calculó a las
  04:09 del 2, trece horas dentro de la ventana. Se cierra con
  `autolot_source`, el sha del Excel guardado al lado del modo, que cubre los
  tres casos —encenderlo, cambiar la tabla de riesgo y apagarlo—. Lo exige
  `test_turning_autolot_on_does_not_wait_a_day`.
- **Un escenario ya medido sobrevive al cambio de mes** (2026-11-02).
  `_scenarios` sólo calcula el mes en curso y el anterior, y `build` reemplaza
  `months_at` entero: en diciembre la fila de octubre desaparecía y recalcular
  su tarjeta la dejaba en «sin medir», justo lo que los escenarios existen
  para no enseñar. `carried_scenarios` arrastra **los lotes, no la medida**:
  el mes viejo se vuelve a medir contra las curvas de hoy, así que su fila
  queda al mismo criterio que el resto de la promesa y no hay dos mediciones
  en el mismo fichero. Tope de 24 meses, la ventana del resumen, porque cada
  fila lleva el lote de cada miembro.
- **El tramo auditado dice sus días** (2026-11-02). Un portafolio dado de alta
  el 31 cubría «100 %» de un día y se leía igual que un mes entero. Los días
  van en el valor y, desde la corrección siguiente, el título lo nombra como
  «Tramo auditado».
- **Un tramo dice que es un tramo en cada recuadro** (2026-11-02). El beneficio
  estacional y el histórico están prorrateados a los días vividos, pero el
  drawdown conserva la referencia de mes entero. Por eso una tarjeta parcial
  nombra «Objetivo del tramo (17 días)», «Beneficio histórico del tramo» y
  añade «mes entero» a sus dos drawdowns de referencia; llamar a 153,77 USD
  «media de los octubres» mientras la frase nombraba 280,40 USD era correcto
  en la cuenta y falso en la etiqueta. «Mes auditado» pasa a «Tramo auditado».
  El título de la tarjeta lleva además el intervalo, por ejemplo
  `(tramo 15-31)`; en un mes natural completo no añade nada.
- **El autolote no queda en una coletilla** (2026-11-02). La tarjeta lleva una
  chapa `AUTOLOTE ACTIVO` y cada portafolio conserva y enseña su propia marca.
  La marca de cuenta sigue siendo la unión de sus usos; en resúmenes antiguos
  con un único uso sirve además de respaldo para poder rotularlo.
- **El tramo empieza a las 00:00 del día que nombra** (2026-11-02). La tarjeta
  de Christmas_Axi decía `(tramo 15-31)` pero `_performance` cortaba a la hora
  exacta de `portfolio_created_at`, 19:34:55. Así eliminaba 27 movimientos y
  98,94 USD del propio día 15: el archivo guardado daba 140,68 USD desde el día
  completo y la tarjeta enseñaba 41,74 USD. El resumen mensual ahora compara
  fechas, igual que `live_account_monthly.active_totals`; la hora sólo registra
  cuándo se guardó, no define un tramo visible por días. El criterio sube a v9
  para obligar a recalcular las fotos guardadas con el recorte incorrecto.
- **`Profit` no es el resultado neto** (2026-11-02). En Profalgo_Robo, tramo
  15–31, el informe nativo da Profit −160,85 EUR, comisión −10,63 EUR y swap
  −6,54 EUR: el efecto sobre el saldo es −178,02 EUR. El usuario pidió que el
  valor principal sea exactamente la columna Profit. Desde `balance-series-v5`
  se guardan ambas series: la tarjeta enseña Profit, mientras los objetivos,
  bandas, rachas y drawdown siguen usando el neto compatible con el backtest.
  El aviso muestra ambos importes y los títulos distinguen «Profit MT5» de
  «Objetivo neto». El criterio sube a v10 para rehacer las frases guardadas.
- **La reproducción nombra sólo las estrategias malas** (2026-11-02). Un
  agregado como «1 NO REPRODUCE» no permite actuar. Debajo de cada portafolio
  el resumen lista cada fila pendiente `AVISO` o `NO REPRODUCE` como
  `estrategia · símbolo · veredicto`. No enumera las que reproducen ni conserva
  una insignia «reproduce»; sin estrategias malas no pinta nada. Usa
  `summary.flagged`, la misma lista ya filtrada por Análisis, sin copiar el
  criterio de reproducción ni volver a calcularlo.
- **Cuentas en real y el resumen comparten la medición** (2026-11-02).
  `live_account_performance.monthly_performance` arma en ambos sitios la pareja
  de filas: Profit MT5 visible y resultado neto comparable. El resumen puede
  recortar primero al tramo de alta, pero no vuelve a definir qué suma cada
  métrica. Quién decide si el Profit se conoce también es compartido:
  `account_series`, y no un `if` en cada pantalla —el resumen tenía el suyo
  después del cálculo, anulando el Profit ya puesto—. Portafolios activos,
  Análisis y la reproducción del resumen comparten además
  `LiveAuditReproduction.strategyGroups`, los filtros de operaciones,
  `numbers`, `verdict` y `compare`. Lo vigila
  `tests/repository/test_live_screens_share_primitives.py`.
- **Un Profit desconocido tumbaba la portada entera** (2026-11-02, el mismo
  día). Separar el Profit visible del neto hizo `None` un valor legítimo de
  `profit`, y `live_account_schedule.convert` seguía haciendo `float(amount)`.
  Una sola cuenta archivada antes de que existiera la columna Profit lanzaba
  `TypeError` dentro del bucle de cuentas de `/api/live-accounts`: la respuesta
  moría entera, **ninguna** tarjeta llegaba al navegador y la pantalla se
  quedaba colgada. En el log del manager se ve como un `TypeError` por minuto
  y ni un solo `200` de esa ruta, hasta que un sondeo rellenó la columna de esa
  cuenta y dejó de pasar solo. Ahora `convert` devuelve `None` tal cual, y
  `_totals` sólo acusa de «sin cambio» a la moneda cuyo importe **sí** se
  conocía: un Profit que falta no es un cambio que falte.

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
| `tests/live_audit/test_monthly_summary.py` | Lo que los hechos acaban diciendo: cobertura unida y no sumada, drawdown inédito, que SIN CONCLUSIÓN tape a CAMBIAR, que el mes se juzgue contra la banda de los suyos y que con autolote diga por qué no se juzga. |
| `tests/live_audit/test_monthly_summary_build.py` | Lo que `monthly_summary_build` mide antes: qué tramo entra en un mes parcial, a qué lotes se puede comparar y qué meses cuentan en la racha. |
| `tests/manager/test_monthly_summary_screen.py` | Que el panel pinte y no decida, que sus rutas y su almacén se porten, que el enlace de la campana lleve a una página que existe, que «Obtener lo que falta» sólo esté vivo cuando falte y que la barra use las clases de `cron-activity`. |
| `tests/manager/test_header_actions_order.py` | El sitio del botón en la barra de las dos pantallas. |
