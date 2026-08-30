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
| Sets cuyo candidato ya no existe | Sin informes no hay nada que reconstruir. Se nombran en el resultado (`unresolved`) en vez de desaparecer. |
| Mes objetivo, si el nombre no lo lleva | No es un campo del resumen: viaja en el nombre («Moderado \| Mes 08 \| …»). Sin él, un mensual se evaluaría sobre la curva completa; `_imported_target_month` lo extrae de ahí. |

Un nombre de set que aparece en dos candidatos distintos se marca `ambiguous` y
se deja fuera: elegir uno al azar comprometería el set equivocado.

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

`tests/test_portfolio_import.py` (12): parseo del resumen, carpeta y ZIP leyendo
lo mismo, ida y vuelta completa hasta `save_proposal`, sets comprometidos después
de importar, números recalculados y no copiados, y los errores con mensaje.
`tests/test_static_portfolios.py::PortfolioImportScreenTests` fija el botón y el
transporte en los tres ámbitos.
