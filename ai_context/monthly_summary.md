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
  **contando meses de la propia serie** («de los 72 meses, 9 fueron peores») en
  vez de suponer una normal. Es el método que `live_audit_degradation_evidence`
  ya usa con el beneficio por operación, por la misma razón: la distribución
  mensual tiene la cola izquierda gorda y rachas.
- **El drawdown del mes contra los drawdowns mensuales del backtest.** Era lo
  que la última sección de `live_audit_degradation_evidence.md` daba por no
  medido en absoluto, y es la familia de criterios con la que de verdad se
  retira una cartera. `beyond` es más hondo que el peor mes que hizo nunca;
  `extreme`, por encima del 95 % sin llegar al peor.
- **La racha de meses por debajo**, que es lo que convierte un mes flojo en una
  decisión. Un mes sin promesa **corta** la racha en vez de saltarse: una racha
  con un agujero no es una racha, es dos trozos.
- **Las estrategias que degradan**, traídas de donde el vigilante ya las dejó
  archivadas. No se vuelven a medir: sería un segundo criterio para el mismo
  veredicto.
- **La cobertura**, que tapa a todos los demás. Con medio mes sin auditar el
  veredicto no es MANTENER, es SIN CONCLUSIÓN: los números salen igual de
  bonitos y nada en ellos dice que están incompletos.

## Decisiones que no se deducen del código

- **No lanza nada.** Los otros dos crones llevan candado doble porque lanzar
  entra en un agente y le pausa el pipeline; éste sólo lee ficheros del disco
  del manager. El interruptor decide si se calcula solo, no si algo peligroso
  puede pasar, y por eso **nace armado** al revés que aquéllos.
- **La unidad es la cuenta real, no el portafolio.** El beneficio y el drawdown
  del mes son de la cuenta; repartirlos entre dos portafolios que la comparten
  exigiría inventar un reparto. La tarjeta los nombra y les cuelga lo que sí es
  suyo, igual que `live_account_dashboard`.
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
| `tests/live_audit/test_monthly_summary.py` | El criterio: racha cortada por el hueco, drawdown inédito, cobertura unida y no sumada, y que SIN CONCLUSIÓN tape a CAMBIAR. |
| `tests/manager/test_monthly_summary_screen.py` | Que el panel pinte y no decida, que sus rutas y su almacén se porten, y que el enlace de la campana lleve a una página que existe. |
| `tests/manager/test_header_actions_order.py` | El sitio del botón en la barra de las dos pantallas. |
