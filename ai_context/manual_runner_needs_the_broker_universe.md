# Un sufijo sin universo se aplica a ciegas (`XAUUSD -> XAUUSD.r`)

## Síntoma (2026-10-29, nodo Vantage)

«Generar histórico OHLC» de un portafolio manual terminaba sin histórico. El
log del runner decía:

```
ForceSymbol: XAUUSD -> XAUUSD.r
```

`XAUUSD.r` no existe en ese servidor: Vantage escribe el oro `XAUUSD+`. No era
la validación del universo ni el `.set`, era la orden que el nodo arma.

## Causa

El nodo construía el comando contra `run_tests.py` con los tres sufijos del
broker y **sin** `--symbol-universe`:

```
--symbol-suffix .r  --symbol-futures-suffix ft.r  --symbol-shares-suffix +
```

`assets/<broker>_assets.ini` es la única fuente que dice cómo escribe cada
símbolo ese servidor. Sin ese fichero, `run_tests_symbols.apply_symbol_suffix`
no tiene nada que consultar y pega el sufijo general a cualquier cosa. Con él,
`XAUUSD` resuelve a `XAUUSD+` y `EURUSD` a `EURUSD.r`.

El fallo estaba en `_add_symbol_options` de `manager_node_runtime/node_commands.py`,
que añade mapas y sufijos y nunca el universo, y lo heredaban sus dos llamantes
que invocan al runner directamente: `manual_portfolio_history.build_history_command`
(histórico OHLC manual) y `manual_check.build_manual_check_command`
(comprobación manual).

## Por qué no lo cubría el pipeline normal

Las etapas de generación no pasan por ahí: llaman a `ubs_agent.py`, que arma su
propia orden contra el runner y **sí** añade `--symbol-universe` desde su
`--assets` (`ubs_agent_variants.py`). Por eso el mismo broker funcionaba en
generación y fallaba sólo en los dos flujos manuales.

## La corrección

`_add_symbol_universe_option(command, project, broker)`, en el mismo módulo pero
**aparte** de `_add_symbol_options`. Dos razones:

- `_add_symbol_options` también arma la orden de `ubs_agent.py`, que no declara
  `--symbol-universe`; metérselo ahí lo mata con `unrecognized arguments`
  (`declared_cli_options` devuelve `None` para una fachada sin literales, así
  que `filter_supported_options` no lo filtraría —ver
  `ubs_agent_cli_split_breaks_option_sniffing.md`).
- Sólo se manda cuando la orden ya lleva sufijos. El universo es además la señal
  de «el broker ya no ofrece esto»: `ini_symbol_missing_from_universe` hace que
  `run_tests.py` omita los sets cuyo `Symbol` no esté listado. Donde no hay
  sufijos que resolver, mandarlo sólo añadiría backtests saltados en silencio
  por un inventario viejo.

## Dónde vive

En el runtime de cada agente, no en este repositorio: el manager sólo reenvía
el lote. Commits hermanos del 2026-10-29: `c53f081` (Vantage), `69d4c42` (AXI),
`ac66697` (RoboForex/`dev`). IC no se pudo tocar —su checkout es de otro perfil
de Windows y esta sesión no puede leerlo— y FUTURE está congelado.

Guarda: `test_manual_runner_commands_carry_the_broker_universe_on_every_fork`
en `tests/node/test_node_runtime_fork_parity.py`. Regresiones en el agente:
`test_history_sends_the_broker_universe_so_the_suffix_is_not_applied_blind` y
`test_the_check_sends_the_broker_universe_with_its_suffixes`, que resuelven el
símbolo con el código del propio runner en vez de dar por buena la lista de
argumentos.

## De paso: una copia que no se deja leer no es divergencia

`_reachable_forks` daba por alcanzable el checkout de IC porque
`portfolio_save.py` se deja mirar; la carpeta niega la lectura, `glob` devolvía
vacío sin error y cada prueba reventaba con `PermissionError`. Ahora se intenta
leer el fichero antes de aceptar la copia, y la lista de «no verificadas» sale
de las que de verdad se comprobaron.
