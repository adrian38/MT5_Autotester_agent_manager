# UBS: torneos repetidos por antirrelleno (2026-09-06)

## Evidencia de la revisión

El usuario detuvo una generación tras unas doce horas y lanzó otra sin cambiar
configuración. El log local disponible de la anterior,
`manager_full_history_generate_20260906_141002.log`, cubre 14:10:02–21:17:45
UTC (7 h 7 min); no permite acreditar por sí solo las doce horas completas.
Está en `portfolio_logs/` de la copia ICTrading autorizada.

La carga terminó a las 14:15:53: 757 estrategias cargadas, 482 elegibles.
Se inició la etapa 4/5 y se registraron 30 entradas «ronda 1» del torneo
experimental, reduciendo el pool de 482 a 400. No llegó a la etapa 5/5.
La última línea es «DETENIDO por el usuario».
El nuevo log `manager_full_history_generate_20260906_211759.log` arranca
a las 21:17:59 UTC y alcanza la final de su primer torneo a las 21:35:41.

## Causa

`_locked_full_proposals` pasa el torneo experimental completo como callback a
`_optimize_without_recent_fillers`. El helper elimina las asignaciones cuyo
aporte reciente no alcanza el 5 % y vuelve a ejecutar el callback sobre TODO
el pool restante. Cada composición puede introducir otros fillers: eliminarlos
en lote por resultado no limita el número de torneos. No hay presupuesto de
tiempo ni de reintentos en ese bucle, y tampoco un mensaje que explique el
reinicio por antirrelleno. El aviso aparece solamente al devolver el resultado.

Reproducción aislada con callback sintético: un core y treinta fillers,
seleccionando un filler distinto por resultado, causan 31 llamadas al
optimizador. Es un bucle finito por agotamiento del pool, pero potencialmente
muy largo. No demuestra que la configuración sea inviable.

## Proceso y alcance

Confirmado con `inspect.getsource` dentro de `mt5-autotester-manager`:
ejecuta `/app/mt5_manager/portfolio_service.py` y contiene ese mismo bucle.
Este cálculo ocurre en el manager Docker, aunque sus logs se escriben en IC.
No es una escritura UBS ejecutada por el fork `manager_node_runtime`.
El helper también tiene llamadores en el mensual; una corrección compartida
debe comprobar ambos scopes y conservar el filtro de aporte reciente.

Revisión sin modificar código ejecutable, configuración ni ejecución activa.
Una corrección debe evitar relanzar ilimitadamente la búsqueda global, mantener
las restricciones al refinar la composición y explicar los reintentos en vivo.
Reducir solamente el presupuesto profundo no elimina la causa.
