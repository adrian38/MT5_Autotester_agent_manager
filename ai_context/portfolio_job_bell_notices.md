# La campana avisa también de los cálculos de portafolio

Petición del usuario (2026-10-20): «cuando se genere un portafolio debe salir
una notificación push, al igual que un arreglo o mejora». La campana y el aviso
del escritorio ya existían, pero sólo sabían de auditorías en vivo
(`live_audit_cron_and_bell.md`).

## Por qué el aviso va donde termina el cálculo, y no donde se guarda

Generar, arreglar o mejorar dura horas. Lo que el usuario se pierde es el final
del **cálculo**, no el guardado: cuando guarda ya está delante de la pantalla
eligiendo variante. Por eso el aviso sale en `_mark_job_completed`,
`_mark_job_failed` y `_mark_job_stopped` de `portfolio_coordinator_core.py`, y
dice cuántas propuestas quedaron listas para revisar y guardar.

## Esto sí lo ejecuta el manager

Excepción a la regla de «el nodo NO ejecuta este repositorio»: `action ==
"generate"` y las operaciones guardadas llaman a
`PortfolioCoordinator.start[_saved_operation]`, que levanta un hilo **en el
manager**. Lo que viaja al agente es el guardado, no el cálculo. **No hay nada
que portar a `manager_node_runtime/`.**

## Reparto

| Módulo | Qué hace |
| --- | --- |
| `portfolio_job_notices.py` | Sólo texto y enlace: título por operación y estado, mensaje, nivel y a qué pantalla lleva. |
| `portfolio_coordinator_core.py` | `_announce_job`, llamado desde las tres salidas del trabajo. |
| `manager_server.py` | Le presta la campana al coordinador (`portfolios.notifications`). |
| `live_audit_notifications.py` | `add()` acepta `link`/`link_text`. |
| `static/live_audit_schedule.js` | Usa ese enlace si viene, y habla del manager y no sólo de auditorías. |

## Tres decisiones que no se deducen del código

- **Un título entero por operación y estado**, no un verbo compuesto. En
  español sale mal: «la generación» contra «el arreglo». El título es lo único
  que se lee en el aviso del escritorio, así que está escrito a mano en
  `OPERATION_TITLES`, también para `reoptimize` y `complete`: el usuario nombró
  tres operaciones, pero son la misma espera larga y filtrarlas sería arbitrario.
- **`_announce_job` no deja escapar ninguna excepción.** Corre dentro del `try`
  del worker: un fallo al escribir el diario saldría por su `except` y marcaría
  como **fallido** un cálculo que había terminado bien. Hay una prueba con un
  almacén que revienta a propósito.
- **El aviso trae su propio enlace.** El de una auditoría se deduce de
  `node_id`+`audit_id`; un cálculo no tiene uso al que enlazar, pero sí una
  pantalla (`/portfolios.html`, `/portfolios_monthly.html`,
  `/portfolios_grid.html`) con su nodo ya elegido.

## El script tiene que estar en la pantalla del cálculo

`Notification` es del navegador: avisa mientras el manager esté abierto en
alguna pestaña **con el script cargado**. La pestaña que el usuario deja puesta
mientras genera es la del portafolio, así que las tres pantallas de portafolio
cargan ahora `live_audit_schedule.js`. Como el script se monta entero sobre
`.header-actions`, eso les añade también el botón «Auditoría programada»: es
intencionado, la cita y los avisos son del manager entero y no de una pantalla.

El retardo es el del sondeo de la campana, 60 s. Para un cálculo de horas no
importa, y evita un segundo canal que diga lo mismo.
