# Autolote histórico en el auditor real

## Caso que lo motivó

El portfolio 148 de RoboForex usa EAs cuyo lote cambia con el crecimiento de la
cuenta. Un único `real_strategy_lots` por estrategia no puede describir un
periodo en el que el lote cambió varias veces, y el emparejado interpreta esos
cambios como operaciones ajenas o desviaciones de volumen.

La configuración admite ahora un Excel `.xlsx` con las columnas
`ESTRATEGIA`, `Dd_0.01` y `%_riesgo`. El manager extrae y guarda las reglas por
uso del portfolio junto con el nombre y la huella del fichero; no conserva el
binario. Acepta porcentajes guardados como número o como texto, como ocurre con
`11.5` y `1.5` en el fichero que originó el cambio.

## Cálculo y precedencia

Para cada pareja candidata el lote esperado se calcula en el instante de la
apertura real:

`saldo × (% riesgo / 100) × 0,01 / DD_0.01`

El resultado se redondea al paso más cercano con mitad hacia arriba y nunca
baja del mínimo publicado por el broker. El detalle de la operación conserva
saldo, riesgo, DD, lote bruto, lote ejecutable y la fuente `autolot` para que el
informe explique el veredicto.

La columna `ESTRATEGIA` del Excel contiene el nombre del `.set` sin extensión,
mientras las operaciones del auditor se identifican por `candidate_id`/magic
(`ROBOFOREX/ECN:3758`, por ejemplo). Antes de comparar, el manager enlaza cada
regla con su miembro mediante `set_name`, `set_id` o `set_path` y usa desde ahí
el `candidate_id`. Buscar la regla directamente con el magic degrada todas las
filas al lote manual aunque el Excel tenga las quince estrategias.

La precedencia es: autolote si está activo y la estrategia figura en el Excel;
lote real manual si no hay regla; lote del tester como último respaldo. Activar
autolote sin reglas o sin saldo histórico es error visible: nunca se degrada en
silencio al lote estático.

## De dónde sale el saldo sin tocar el nodo

El HTML nativo de MT5 ya es un artefacto obligatorio de cada auditoría y su
tabla `Deals` publica el saldo acumulado. El manager lo descarga una sola vez
por `sha256`, verifica la huella y guarda en memoria una caché acotada de 32
historias. Para cada apertura usa el último saldo informado en ese instante;
si la apertura precede a la primera fila disponible, usa el primer saldo del
informe.

Las horas de esa tabla son UTC, pero el HTML las imprime sin sufijo de zona;
la materia prima JSON del nodo sí lleva `+00:00`. Ambos lados se normalizan a
UTC antes de ordenar o comparar. No comparar directamente los `datetime` del
parser del HTML con los revividos del JSON: Python rechaza mezclar fechas sin
zona y con zona, y convertirlas a la zona local desplazaría el saldo elegido.

Esto mantiene el cambio únicamente en el manager. La configuración `autolot`
se retira del payload que lanza el nodo; el runtime de RoboForex no necesita un
port. El nodo sigue observando y publicando hechos, y el manager aplica el
criterio al reanalizar la materia prima guardada.

La simulación de lotes de la pestaña Análisis ofrece además el botón
`Autolote`. Reescala cada operación demo con el `volume_expected_real` que el
veredicto calculó para su apertura; no reduce el periodo a un único lote por
estrategia. La curva real conserva lo ejecutado y las operaciones demo sin un
cálculo histórico conservan también su volumen original. Es una simulación de
la vista: no modifica el perfil ni el veredicto.

## Efecto en el emparejado

Las bandas de pertenencia se amplían con todos los lotes dinámicos esperados
durante el periodo para no descartar una operación antes de compararla. Después,
`_pair_cost`, la validación de volumen y el escalado de PnL usan el lote de esa
apertura concreta. Así dos operaciones de la misma estrategia pueden esperar
lotes diferentes dentro de una sola auditoría.
