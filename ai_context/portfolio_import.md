# Importar un portafolio exportado

## Para qué

Se exporta un portafolio, se borra del manager, y meses después hace falta que
sus sets **sigan contando como usados** para que la siguiente generación no los
repita. Sin importarlos, `used_set_paths` no los ve, `exclude_used_sets` no
excluye nada y el optimizador vuelve a proponer las mismas estrategias.

## Se lee lo que ya escribe la exportación

No hay formato nuevo. La carpeta que produce `export_portfolio` lleva los `.set`
copiados y un `PORTAFOLIO_<id>_resumen.txt` con capital, DD objetivo y usado,
net total y una fila por estrategia (perfil, cuenta, símbolo, timeframe,
unidades, lote, nombre del set). Eso vale para **exportaciones ya hechas**, que
es justo lo que queda cuando el portafolio ya se borró.

La tabla del resumen se escribe con anchos fijos, así que se corta por posición;
si el corte no deja un nombre de set creíble —un símbolo largo desplaza las
columnas— se reparte por la derecha, donde el orden sí es fijo. El perfil se
trunca a 12 caracteres (`Moderado Grid` → `Moderado Gri`), de modo que la
variante se resuelve por prefijo, nunca por igualdad.

## Por qué el resultado no es una copia degradada del texto

Del resumen sale **solo la composición**: qué set, con cuántas unidades, en qué
variante. Todo lo demás se recalcula desde los informes MT5 del candidato, que
siguen en el proyecto del agente: `load_robust_sets_from_rows` los vuelve a
parsear y `evaluate_portfolio` los evalúa con las mismas funciones que un
cálculo nuevo. Curva, DD valle, DD puntual, aporte por estrategia, flotante y
bootstrap de estrés son medidos, no copiados.

Por eso la importación termina en `save_proposal`, el mismo camino que guarda una
propuesta recién calculada: la fila resultante es indistinguible de una normal,
con sus variantes A/M/C, su `metrics_json` y sus miembros. Hay una prueba que lo
fija comparando el net guardado contra el del resumen: si alguna vez copiara el
texto, el número coincidiría y la prueba fallaría.

## Lo que no puede traer

| Falta | Por qué |
| --- | --- |
| Margen por estrategia | La exportación no lo lleva y depende de la cuenta y de las specs del símbolo en el momento del cálculo. Queda a 0. |
| Registro de decisiones del optimizador | Es la historia de una búsqueda que aquí no ocurrió: la composición viene dada, no elegida. |
| Sets cuyo candidato o informes ya no existen | La composición, unidades y lotes se conservan. La estrategia queda marcada sin métricas y el cálculo global avisa que beneficio/DD son incompletos. |
| Mes objetivo, si el nombre no lo lleva | No es un campo del resumen: viaja en el nombre («Moderado \| Mes 08 \| …»). Sin él, un mensual se evaluaría sobre la curva completa; `_imported_target_month` lo extrae de ahí. |

## Identidad de una mejora exportada

Desde 2026-09-13 la cabecera del resumen conserva explícitamente el portafolio
origen, el modo, la prioridad de selección y el número de incorporaciones. Al
importar UBS normal, esos campos vuelven a `metrics.inputs` y a
`seasonal_validation.portfolio_improvement`; por eso la fila se guarda como una
mejora independiente y la interfaz recupera el nombre, la prioridad, el `+N` y
la comparación con el original.

Las exportaciones anteriores pueden recuperar origen y modo si su línea
`Portafolio:` ya decía «Mejora de #X | Modo»; si el original aún existe, también
se reconstruye `added_count` comparando composiciones. La prioridad no se deduce
de la composición porque varias prioridades pueden producir el mismo resultado.
Este comportamiento se limita a `full_history`; mensual continúa sin cambios.

### Mejora de una mejora y etiquetas portables (2026-09-13)

Una mejora de segundo nivel ya no pierde su historia al viajar. El resumen
conserva la etiqueta visible, un UUID portable de la cartera, UUID del padre y
de la raíz, profundidad, cadena ordenada de ancestros y el snapshot completo del
padre inmediato. La importación vuelve a guardar esos campos tanto en `inputs`
como en la auditoría; por ello una cadena `#14 -> #36 -> nueva` sigue mostrando
«Mejora del portafolio #36 | modo Moderado», raíz `#14` y nivel 2 aunque el
destino le asigne otro id local o no tenga guardado el #36.

Los ids `#N` se conservan como etiquetas históricas, pero la identidad entre
memorias se apoya en UUID. Para carteras antiguas sin UUID, la primera
exportación calcula uno determinista a partir de la fila y su composición. Los
resúmenes antiguos, sin estos campos, siguen entrando con origen/modo como
antes. El snapshot exportado permite comparar contra el padre sin consultar una
fila local que casualmente tenga el mismo número.

La escritura del nombre real ocurre en el nodo embebido. En `dev` se actualizó
la copia ICTrading autorizada para usar `improvement_label` cuando existe; AXI y
RoboForex quedan pendientes del port que realiza el usuario.

Un nombre de set que aparece en dos candidatos distintos se conserva sin
métricas y se marca `ambiguous`: no se elige uno al azar.

## La composición exportada es autoritativa

Importar no vuelve a calcular la elegibilidad del pool. El ZIP representa una
decisión ya guardada y debe restaurar todos sus miembros aunque una reparación
posterior haya cambiado a `rejected` el veredicto de robustez, Final Tick o
Final Tick 6M. Esos veredictos actuales se muestran como advertencia, pero no
se usan para recortar la composición.

Por eso la resolución usa `PortfolioSource.import_candidate_rows`, separado de
`candidate_rows`: este último conserva el filtro estricto de las cuatro etapas
para cálculos nuevos. La separación corrigió el caso real del
`PORTAFOLIO_5_ICTRADING.zip`, cuyo resumen contenía 7 sets A/M/C pero se había
restaurado como portafolio #15 de solo 4 porque XAUCHF estaba rechazado en Final
Tick 6M y USDJPY/XAGUSD en robustez. Con el inventario de importación se
reconstruyen los 7 en las tres variantes, sin unresolved, ambiguous ni skipped.

## Composición exacta aunque falten filas o informes (2026-09-15)

La importación no puede guardar un subconjunto del ZIP. El caso real fue
`PORTAFOLIO_46_resumen.zip`: contenía 10 sets, incluido
`XAUUSD_H4_GOLD_XAUUSD_H4_GOLD_5e988f6b_g002_s013_v008.set`, pero la memoria
actual de RoboForex ya no tenía su fila de robustez. El importador creó el #122
con 9 sets y omitió XAUUSD. El mismo riesgo existía en AXI e ICTrading para
cualquier candidato o informe ausente/ilegible.

La ausencia de robustez de ese XAUUSD no fue pérdida del fichero. Antes de la
corrección de contrato de normalización de RoboForex del 2026-08-10, el candidato
#4348 tenía net normalizado 411,98, estado base `accepted` y robustez `accepted`.
La normalización correcta de metales aplicó factor 0,227753: el net normalizado
quedó en 93,83, por debajo del mínimo 100, y el estado base pasó a `rejected`
con motivo `net_profit`. La limpieza coherente de etapas borró entonces sus
filas de robustez, Final Tick y posteriores; el HTML de robustez
`robust_004348_...htm` permanece en disco y la memoria previa a la corrección
conserva la fila antigua. Por tanto, la rareza es histórica pero explicable: el
portafolio se creó con el criterio de normalización antiguo y se importó contra
el veredicto vigente corregido.

La composición del ZIP siempre se conserva. La reconstrucción sigue este orden:

1. usa la fila de robustez vigente cuando existe;
2. si la fila fue limpiada, busca el HTML histórico determinista
   `reports/robust_<candidate_id>_<set>.htm` y recalcula con él;
3. si tampoco existe un informe legible, guarda de todos modos el miembro con
   símbolo, timeframe, unidades, lote y set del resumen, pero con métricas a 0 y
   `floating_dd_source="No reconstruido al importar: ..."`.

El tercer caso añade `import_calculation_complete=false`, enumera los sets en
`import_unmeasured_sets` y avisa que el beneficio y DD globales sólo representan
las estrategias medibles. No se atribuyen métricas inventadas. La recuperación
y las marcas viven en el manager antes de `/api/v1/portfolios/save`; protegen
todos los nodos sin cambios en sus `manager_node_runtime/`.

## El perfil de una mejora no viaja en la tabla (2026-09-17)

`save_proposal` guarda un portafolio de **una sola variante** —una mejora, o
cualquier mensual— con `variant_key` y `variant_label` vacíos: la variante es la
fila entera, no una de tres (`key = str(proposal["key"]) if bundle else ""`).
La exportación leía el perfil de ese miembro, así que la columna PERFIL del
resumen salía **en blanco**, y al importar `variant_key_for("")` no reconocía
ningún modo y devolvía `variant_1`. En una mejora eso chocaba con la
comprobación de identidad y abortaba:

> La identidad de mejora de la exportación no coincide con su composición:
> esperaba solo el modo Agresivo

Casos reales: `PORTAFOLIO_120_aggressive` y `PORTAFOLIO_121_aggressive` de
RoboForex, imposibles de reimportar después de borrar sus filas. Un mensual no
fallaba, pero se guardaba con `portfolio_type="variant_1"`.

El modo sí está en la cabecera (`Tipo:` y, en una mejora, `Mejora modo:`), que
es de dónde se toma ahora cuando el perfil viene vacío. Además la exportación
rellena la columna desde el tipo del portafolio, para que los resúmenes nuevos
se expliquen solos. Ambos extremos son del manager: ningún nodo cambia.

### Una mejora no puede ser su propio origen

`PORTAFOLIO_121` traía como `Portafolio UID` el de su origen `#120`, el mismo
valor que su `Mejora origen UID`. Conservarlo dejaría dos filas importadas con
la misma identidad y una cadena que se compara consigo misma, en silencio. La
importación descarta ese UID repetido, avisa y deja que la fila reciba
identidad propia; el enlace al padre se conserva en `improvement_parent_uid`.
El origen del UID duplicado está en la creación de la mejora encadenada, no en
la importación: no se ha podido reproducir porque el usuario ya había borrado
`#120` y `#121` de la memoria de RoboForex.

## Cuánto cuesta importar y por qué (2026-09-17)

Medido con `PORTAFOLIO_121` (18 sets) contra la memoria real de RoboForex,
proceso frío:

| Etapa | Antes | Ahora |
| --- | --- | --- |
| `import_candidate_rows` | 7,5 s — 70.065 filas | 0,8 s — 18 filas |
| Parseo de informes MT5 (`cached_report`, 63 ficheros, 55 MB) | 8,5 s | 8,5 s |
| `bootstrap_valley_drawdown` | 3,1 s | 3,1 s |
| **Total** | **19,5 s** | **12,3 s** |

El inventario de importación no filtra por veredicto —es su contrato—, así que
preparaba la memoria **entera** para resolver 18 nombres: además de las 70.065
filas, cada una sin robustez vigente (51.231) sondea hasta dos `is_file()`
buscando su informe histórico. Son ~100.000 accesos a disco, y en el manager
ese disco es el recurso de red del agente. Ahora `build_import_proposals` pasa
los nombres del resumen y el filtro se aplica antes de resolver rutas y sondear
informes; las filas relevantes son exactamente las mismas, con una prueba que
lo fija.

Lo que queda es trabajo real y compartido con cualquier cálculo: parsear los
informes de los sets (55 MB de HTML para 18 estrategias, cacheados por
mtime/tamaño mientras viva el proceso del manager) y el bootstrap de la curva.
Aparte, `invalidate_after_exclusion` invalida el snapshot de la memoria al
terminar, así que la primera lectura posterior vuelve a copiarla: eso es la
recarga de la pantalla, no la importación.

## Transporte: el reflejo de la exportación

| `export_mode` | Exportar | Importar |
| --- | --- | --- |
| `folder` (por defecto) | selector nativo del manager | selector nativo (`choose-import-folder`) y ruta de carpeta |
| `download` | el navegador descarga el ZIP | el navegador sube el ZIP en base64 |

Cubrir solo uno dejaría la función inservible en el otro despliegue. La lectura
del ZIP y el resumen del resultado viven en `mt5_manager/static/portfolio_transfer.js`,
compartido por las tres pantallas; el botón y su recarga son de cada una, igual
que los de exportar.

## Quién escribe

En Portafolio UBS el manager reconstruye las propuestas desde los informes, pero
no escribe la memoria: la envía por `/api/v1/portfolios/save` al nodo, igual que
un guardado normal. Es el nodo quien ejecuta `save_portfolio_payload` contra su
base WAL local. Intentar `save_proposal` desde el manager falla en Docker/bind
mounts o SMB con `disk I/O error`; ver `portfolio_write_needs_the_node.md`.

Este cambio está acotado a `full_history`. UBS mensual queda fuera del alcance y
conserva su comportamiento anterior hasta autorización explícita. Grid también
conserva su base propia en el manager mediante `_persistence_source`.

## Guardas

- Un A/M/C guardado siempre comparte composición entre variantes (solo cambian
  las unidades) y `save_proposal` lo exige. Si el resumen no lo cumple, la
  importación falla **antes**, nombrando cuántos sets tiene cada variante, en vez
  de dejar que el guardado falle con un mensaje que no señala al fichero.
- Una carpeta con dos exportaciones dentro se rechaza: importa una cada vez.
- Una carpeta sin `PORTAFOLIO_*_resumen.txt` dice exactamente qué esperaba.

## Pruebas

`tests/test_portfolio_import.py`: parseo del resumen, carpeta y ZIP leyendo
lo mismo, ida y vuelta completa hasta `save_proposal`, sets comprometidos después
de importar, números recalculados y no copiados, errores con mensaje, y una ida
y vuelta de mejora encadenada que conserva etiqueta, raíz, nivel y snapshot.
`tests/test_static_portfolios.py::PortfolioImportScreenTests` fija el botón y el
transporte en los tres ámbitos.

## Variante mostrada en un bundle importado (2026-09-13)

La fila principal de un bundle conserva `selected_variant`, pero esa clave dice
qué modo se usó como base para fijar la composición común A/M/C; no identifica
qué modo tiene desplegado el usuario. El detalle UBS normal ya no presenta esa
base como si fuese la variante en uso. Muestra tarjetas Agresivo/Moderado/
Conservador, repinta métricas, estrés, auditoría y miembros con la elegida, y
recuerda la preferencia en el navegador por nodo y portafolio. Si aún no existe
preferencia comienza por Agresivo, la primera variante del bundle. La lista se
actualiza con el mismo neto y deja escrito qué variante está mostrando. Abrir
`Mejorar base` parte de esa misma variante visible, aunque el diálogo permite
cambiarla antes de calcular.

Es una elección de visualización: no reescribe el bundle ni cambia la base de
composición guardada. Sólo afecta a UBS normal; mensual y Grid conservan sus
interfaces independientes. El cálculo y la persistencia no cambian, por lo que
no requiere port a `manager_node_runtime/`.
