# «Experimenta Seeds»: el laboratorio del millón, con el pool hecho de seeds

Pedida el 2026-11-15: «algo parecido a Experimenta 1M pero sólo con las seeds».
Su botón es el séptimo del bloque común (`Experimenta Seeds`, con S mayúscula
por corrección del usuario ese mismo día) y hasta entonces era el hueco apagado
que `main_nav.js` reservaba con `url: ''`.

La pantalla es `/seed_experiment.html` y su API `/api/seed-experiment/`. Todo lo
que mide, cómo mide y lo que no puede medir está en
`experiment_million_lab.md`: **es el mismo motor**. Esta nota sólo cuenta lo que
no se parece.

## Lo único que cambia es de dónde sale el pool

| | Experimenta 1M | Experimenta Seeds |
| --- | --- | --- |
| Origen | `PortfolioSource.candidate_rows()`: las cuatro etapas aceptadas | `load_seed_sets()`: `seed_scores` con `active=1 and accepted=1 and status='accepted'` |
| Tope por broker | `max_candidates_per_node` | `max_seeds_per_node` |
| Criterio del recorte | ronda por símbolo, `candidate_id` más alto | `score` descendente, que es el orden en que las devuelve la tabla |
| Garantía de la estrategia | candidato, robustez, Final Tick continuo y Final Tick 6M | su propio backtest en el agente, y nada más |

Esa última fila es el aviso que la pantalla da en voz alta: lo que sale de aquí
es un **techo optimista**, no una cartera aprobable. La misma pregunta sobre
estrategias que sí pasaron el embudo la responde la pantalla hermana.

## Qué se hereda, y cómo

`SeedExperimentCoordinator` **hereda** de `ExperimentCoordinator`. Los tres
puntos de variación son atributos de clase, no referencias al módulo, y ése fue
el único cambio que hubo que hacerle al laboratorio 1M:

| Punto | Para qué |
| --- | --- |
| `DEFAULTS` y `_normalize` | su formulario y su validador |
| `RESULT_NAME` | `seed_experiment_last_result.json`, porque los dos coordinadores reciben la misma carpeta de `runtime/` |
| `_load_origin_sets` | el pool de seeds en lugar del embudo UBS |

El hilo, el log, `stop`, los montajes de solo lectura de AXI y RoboForex
(`MT5_MANAGER_EXPERIMENT_*_PROJECT_DIR`), el perfil de margen del destino, la
portabilidad, la búsqueda, el veredicto y el guardado son literalmente los
mismos. **Una corrección ahí llega a las dos pantallas**, que es justo lo que se
quería: el laboratorio 1M ya había pagado tres veces el precio de medir mal.

`DEFAULT_SETTINGS` se **deriva** del de 1M quitando `max_candidates_per_node` y
añadiendo `max_seeds_per_node`. Escribir el segundo diccionario a mano habría
dejado que los límites de riesgo de las dos pantallas se separasen sin que nadie
lo notara; hay una prueba que compara los dos conjuntos de claves.

## El cargador de seeds bajó de `prop/` a `experiment/`

`load_seed_sets` vivía en `mt5_manager/prop/prop_experiment_seeds.py`, porque el
primero que leyó seeds fue el laboratorio prop. Ahora lo usan los dos y está en
`mt5_manager/experiment/experiment_seed_sets.py`.

La dirección importa: `prop` ya importaba de `experiment` —su motor y su
coordinador—, así que llevarlo a `experiment` baja la dependencia. Al revés
habría sido `experiment` → `prop` → `experiment`: hoy resuelve de milagro
—`prop_experiment_seeds` no importaba nada de `experiment`— y revienta con el
primer import que se añada en el otro sentido.

## Dos pantallas, un solo JavaScript

`seed_experiment.html` carga `/experiment.js` y `/experiment.css`; su módulo de
rutas sirve **sólo** su HTML. Las dos cosas en las que las páginas se
diferencian las dice el documento, no el script:

- la API, en `data-api` del `<body>`;
- los campos del formulario, que el JS lee de él (`input[type=number]` y
  `select[name]` dentro de `#experiment-form`) en vez de llevar la lista
  escrita. Con la lista escrita a mano, el campo que sólo existe en una de las
  dos pantallas viajaría como `undefined` en la otra.

Lo que esto obliga a recordar: **los `id` de las dos páginas tienen que
coincidir**. El JS busca `#verdict-metrics`, `#pool-rows`, `#monitor` y una
docena más; un `id` que falte no rompe la pantalla a la vista, deja de pintarse
su panel y nada más. Si alguna vez las dos pantallas dejan de poder compartir
el JS, se parte entonces y no antes.

## Endpoints

Los seis de 1M con el prefijo `/api/seed-experiment/`: `config`, `state`,
`log?lines=N` (GET) y `settings`, `run`, `stop` (POST). `max_candidates_per_node`
es aquí un **campo desconocido** y se rechaza con 400, igual que
`max_seeds_per_node` en la otra: el vocabulario de cada formulario es el suyo.

## Lo que todavía no se ha medido

La estructura está puesta y probada en seco: no hay ninguna pasada end-to-end
contra las memorias reales, así que no hay números de referencia como los del
2026-09-10 del laboratorio 1M. Lo primero que haga una pasada de verdad debería
dejarlos aquí, con la fecha, y revisar dos cosas que sólo se ven con datos: si
`pool_limit` (tope 300) da de sí con el volumen de seeds aceptadas de las tres
memorias, y si el recorte por `score` deja fuera símbolos enteros como le pasó
al recorte por fecha del laboratorio 1M.
