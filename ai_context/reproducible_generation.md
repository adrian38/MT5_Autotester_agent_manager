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
