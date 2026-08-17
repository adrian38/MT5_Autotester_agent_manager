# Generación reproducible y reintento sin reporte

## Incidente observado

En la ejecución 83 de AXI aparecieron 30 resultados `no_report` de 90 candidatos.
Los casos se concentraron en la última oleada de workers: MetaTrader terminaba con
código normal, pero no creaba un informe nuevo. El código antiguo reservaba un
segundo intento, aunque solo lo consumía para watchdog o para un informe vacío de
`Model4`; un `Model1` sin informe terminaba directamente como `no_report`.

## Contrato de reintento

Todos los modelos de tester reintentan una vez cuando el proceso termina sin un
informe fresco. Antes del segundo intento se usa el mismo limitador compartido de
reinicios que protege los reintentos por watchdog. Los informes antiguos continúan
sin aceptarse y el segundo fallo conserva el resultado `no_report`.

## Semilla de generación

El formulario, la API y las preferencias del manager aceptan `random_seed` como
entero opcional. Un valor vacío o `null` mantiene el comportamiento aleatorio. El
nodo reenvía el valor al agente como `--random-seed` y lo publica también en
`launch_defaults`.

El flujo de descubrimiento no comparte un único generador aleatorio. La selección
de seeds, targets y timeframes usa un stream determinista por generación, mientras
que cada variante recibe otro stream derivado de semilla, generación, índice de
seed e índice de variante. Así, añadir consumos aleatorios a una mutación no cambia
el routing ni desplaza las variantes vecinas. La versión actual del contrato queda
registrada en `run_config.json` como
`generation-selection-mutation-v1`.

## Semilla vacía: fallo con código 2 (2026-08-17)

El run #124 de ICTrading terminó como `FAILED` un segundo después de arrancar, con
`return_code = 2` y sin ninguna etapa ejecutada. La causa no estaba en la
generación: `_add` de `node.py` construía la orden con `str(value)`, así que un
`random_seed` `None` —lo que deja el diálogo con la semilla vacía— viajaba como el
texto `"None"`. `ubs_agent.py` moría en `argparse`:

```
ubs_agent.py: error: argument --random-seed: invalid int value: 'None'
```

Detalles que costaron tiempo y conviene no volver a investigar:

- Ninguna otra opción lo delataba: `--from-date`, `--symbol-map` y compañía caen en
  `setting(...)`, que devuelve cadena vacía. `--random-seed` es la única sin
  respaldo, así que es la única que llegaba como `None`.
- AXI y RoboForex seguían corriendo con la misma semilla vacía, así que sus nodos
  no están añadiendo la opción. No se puede comprobar desde este equipo: los dos
  corren en `DESKTOP-E2VTFPQ` (192.168.1.152) y aquí no hay copia de su
  `manager_node_runtime/`. Es decir, el fallo aparece **solo** donde el nodo sí
  está portado, lo contrario de lo que sugiere la tarjeta.
- Latente cinco días: el código es del 12-08 (`c80ba2f` aquí, `b0a155a` en el
  agente) y el árbol del agente lo tenía desde el 16-08 a las 02:26, pero el
  proceso seguía con el `node.py` anterior en memoria. Se destapó al reiniciar la
  aplicación el 17-08 a las 12:25. Las reparaciones no lo notaron nunca:
  `build_pipeline_stage_command` no pasa la semilla.
- `_normalize_generation` ya dejaba `None` correctamente; el error estaba después,
  al serializar la orden. La prueba del agente comprobaba la normalización y se
  quedaba a una línea de destaparlo.

Arreglado con `if value is None: return` en `_add`, en el manager y en la copia del
agente. Lo vigilan `tests/test_node.py`
(`test_build_generation_command_omits_the_seed_when_it_is_random`),
`tests/test_node_runtime_fork_parity.py`
(`test_optional_cli_values_are_omitted_instead_of_stringified_on_every_fork`) y
`tests/test_manager_node_regression.py` del agente.

## Copias desplegadas

El contrato está implementado en el manager y en los runtimes embebidos de los
agentes ICTrading, AXI y RoboForex. Como cada agente ejecuta su propia copia de
`manager_node_runtime/node.py`, es obligatorio reiniciarlo para cargar cambios en
el protocolo. También hay que reiniciar el manager para cargar su backend y los
assets web actualizados.

## Verificación mínima

- Un `Model1` que termina sin informe debe lanzar exactamente un segundo proceso.
- Un informe creado en el segundo intento debe producir un resultado válido.
- La misma semilla y configuración deben reconstruir selección y mutaciones.
- Consumir números aleatorios extra en una variante no debe cambiar el target ni
  la variante siguiente.
- Un `random_seed` no entero debe rechazarse antes de iniciar el run.
