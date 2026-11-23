# El nombre de un `.set` no identifica la estrategia

Encontrado el 2026-11-23 midiendo por qué la tarjeta «Sets utilizables por
broker» del laboratorio prop no cuadraba con las pantallas del agente.

## La trampa

El agente nombra cada variante con su posición dentro del run:

```
run_20260813_154334\gen_002\DE40\M15\DE40_M15_NEW_STRATEGIES_MIX_The_ORB_Master_8d32679e_g002_s001_v003.set
run_20260815_161903\gen_002\DE40\M15\DE40_M15_NEW_STRATEGIES_MIX_The_ORB_Master_8d32679e_g002_s001_v003.set
```

`g002_s001_v003` son generación, seed y variante **dentro de su run**, y el
hash intermedio es el de la estrategia base, no el de los parámetros. Dos runs
distintos reutilizan el nombre entero para estrategias diferentes: los dos
ficheros de arriba pesan lo mismo y difieren en `ST1_HL_strength_L`,
`ST1_countback`, `ST1_MinDist_to_HL`, `MaxTrades`, `MinDist_orders`,
`Exit_stop` y `Exit_limit`.

Así que **comparar por `Path(...).name` no es comparar estrategias**. Lo hacía
`_quarantine_names` de `mt5_manager/prop/prop_experiment_pool.py`, y encima
quitaba el prefijo `robust_\d{6}_`, lo que ensancha aún más la colisión:
excluir un set del 13 de agosto tiraba también al homónimo del 15, que nadie
excluyó. Eran seis candidatos de ICTrading (cuatro chocando con exclusiones por
«OHLC ≠ every tick» y dos con una cuarentena manual) que la pantalla «Sets
disponibles por símbolo» sí daba por disponibles.

La clave buena es la de `PortfolioSource._match_key`: resuelve la ruta contra
el proyecto actual —un portafolio guardado puede traer `/data/...` de un
contenedor o `X:\...` de otra máquina— y la normaliza antes de comparar. Es la
que usa `candidate_rows(include_quarantined=False)`, y por eso el inventario
acertaba y el pool prop no.

El síntoma es silencioso en las dos direcciones: por nombre se pierden
estrategias que nadie excluyó, y por ruta cruda no se excluye ninguna porque
las claves nunca casan (lo que ya pasó una vez, y está contado en el docstring
de `_without_quarantined`).

## Dónde sigue viva la suposición

Estos sitios comparan por nombre **a propósito**, porque no tienen ruta común
que comparar, y no son el mismo problema; pero hay que saberlo antes de
reutilizarlos para otra cosa:

- `_allowed_names` y `load_seed_sets(allowed_set_names=...)`: el miembro de un
  portafolio activo y el candidato del que salió viven en rutas distintas.
- El `_logical_name` con el que `_source_inventory` y `load_origin` deduplican
  candidato / seed / manual: la misma estrategia llega por tres caminos con
  tres rutas, así que ahí el nombre es lo único común.

## Dos contadores de lo mismo tienen que descartar igual

De la misma tarjeta salió el otro desajuste: el inventario no miraba el
interruptor «Utilizar grids», que `credible_pool` sí aplica. La tarjeta
prometía 1062 sets utilizables en RoboForex y la fase 1 descartaba 201 nada más
empezar, con un aviso que el usuario sólo ve si lanza el experimento. El
criterio vive ahora en `set_path_is_grid`
(`mt5_manager/prop/prop_experiment_engine.py`) y lo llaman los dos.

Regla general: un contador que anuncia «lo que vas a poder usar» tiene que
aplicar **todos** los descartes que aplica el cálculo real, o es una promesa que
el propio cálculo desmiente un minuto después.
