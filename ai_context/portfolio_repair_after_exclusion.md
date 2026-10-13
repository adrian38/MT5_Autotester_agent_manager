# Arreglar un Portafolio UBS después de excluir estrategias

## Alcance

«Arreglar» pertenece únicamente a Portafolio UBS normal (`full_history`). El
mensual sigue congelado y conserva su interfaz y sus operaciones antiguas.

El botón sólo aparece cuando la variante A/M/C que se está viendo contiene al
menos una estrategia marcada como excluida. Sustituye en esa pantalla a
«Completar portafolio», «Revalidar / optimizar» y «Deshacer recomposición», que
se retiraron porque no se utilizaban.

## Contrato del arreglo

El arreglo crea otro portafolio de una sola variante; nunca modifica el
original. Toma como composición histórica la variante visible y aplica estas
reglas:

1. Las estrategias excluidas no entran en el pool.
2. Todas las no excluidas son obligatorias y no pueden retirarse.
3. Sus unidades y lotes sí pueden cambiar para volver a cumplir el riesgo,
   margen, correlaciones y demás límites guardados.
4. No se hereda ni se fuerza el número de estrategias de la variante original.
   El total final lo decide el optimizador dentro de los límites guardados; las
   candidatas nuevas deben pasar las cuatro etapas aceptadas y los filtros
   guardados: grupos, símbolos deshabilitados, Grid OFF, tres meses positivos,
   uso previo y aporte Final Tick 6M.
5. El resultado sólo se ofrece si conserva todas las no excluidas y cumple los
   límites de riesgo, margen, correlación y construcción heredados.

Los lotes guardados no se pasan como `required_initial_allocations`. Esa opción
hace que el optimizador valide la composición parcial antigua antes de poder
reducirla y falla precisamente cuando, tras las exclusiones, ya incumple un
límite. `required_set_ids` conserva la membresía superviviente; el optimizador
arranca esas estrategias desde su unidad mínima y reconstruye todos sus lotes
dentro de los límites originales.

Una mejora puede haber creado una composición con excepciones efectivas que no
reescriben los ajustes base: `improvement_allow_same_symbol=true` eleva el tope
por símbolo, y su reparto final eleva el tope de sets por grupo y el bootstrap
del grupo hasta el tamaño seleccionado. Al arreglar un derivado de mejora hay
que reconstruir esas tres excepciones desde los metadatos `improvement_*`; usar
otra vez `max_sets_per_symbol=1` o el tope A/M/C por grupo invalida incluso una
unidad de cada superviviente antes de que el optimizador pueda empezar.

La propuesta guarda `seasonal_validation.portfolio_repair` con las retiradas,
las conservadas y las incorporaciones. También conserva las claves
`improvement_*` mínimas como compatibilidad de transporte: los nodos ya
desplegados las usan para guardar una propuesta de una sola variante como una
fila nueva. La etiqueta visible es «Arreglo del portafolio #N», no «Mejora».

## Dónde se ejecuta

El cálculo vive en `mt5_manager/portfolio_repair_service.py` y lo ejecuta el
manager. El nodo sólo recibe el mismo `operation=generate` que ya sabe persistir
para una propuesta individual. No hay endpoint ni criterio nuevo en
`manager_node_runtime/`, por lo que este cambio no requiere port al agente.

## El refinamiento profundo respeta los topes explícitos (2026-10-13)

El refinamiento profundo debe respetar `maximum_active_strategies` cuando un
llamante lo configura explícitamente, igual que el greedy. El soporte general
para ese tope se conserva, pero «Arreglar» no lo rellena con el tamaño del
portafolio original: esa equivalencia fue una interpretación incorrecta del
contrato y producía el rechazo «No se pudo reconstruir el tamaño original».

`_deep_refine_allocations` propaga el máximo opcional. Los movimientos de
adición pueden aumentar unidades de una estrategia ya activa, pero no activar
otra cuando un llamante sí fijó ese cupo; los intercambios siguen permitidos
porque conservan la amplitud. El refinamiento mensual estricto usa su propio
motor y no cambia.

## Exclusión mutua entre cálculos

Un nodo sólo puede tener un cálculo de portafolio activo, aunque el segundo
inicio llegue desde otro scope (UBS, mensual o Grid). El segundo se rechaza sin
tocar el evento de cancelación ni el trabajo en curso y la API responde que el
generador está ocupado y debe intentarse cuando termine. En la pantalla actual
también quedan deshabilitados Generar, Mejorar y Arreglar mientras haya una
operación activa. Iniciar otra generación nunca equivale a detener la anterior.

## Confirmación editable de la herencia (2026-10-13)

«Arreglar» ya no empieza con una confirmación ciega. Antes de lanzar el cálculo
abre un formulario con los valores efectivos heredados por la variante visible:
riesgo, embudo, límites de unidades, filtros, correlaciones, margen, grupos y
símbolos deshabilitados. Todos esos valores se pueden cambiar y la petición del
formulario prevalece sobre los inputs guardados de la variante; la propuesta
serializa esos valores y pasan a ser la herencia de sus futuros derivados.
El formulario sólo presenta ajustes que el motor de arreglo consume. En
particular, no muestra `experimental_full_search`: esa ruta pertenece a la
generación ordinaria de la composición base A/M/C y el arreglo no la ejecuta.

El detalle expone `inherited_inputs`, normalizado también para portafolios
antiguos que no guardaban `metrics.inputs`. En bundles, la interfaz superpone
después los `inputs` de la variante A/M/C visible. El modo no se cambia dentro
del diálogo: identifica la variante cuyas exclusiones se están arreglando.

El cálculo y la persistencia siguen usando contratos ya existentes en el
manager y el nodo. No se añadió endpoint ni criterio de escritura al runtime
del agente, así que este cambio no requiere port a `manager_node_runtime/`.

## Composición rápida (2026-10-14)

El diálogo de Arreglar ofrece una casilla temporal `Composición rápida`. Al
marcarla, el payload fuerza `run_local_search=false` y `search_restarts=0` por
encima de los valores heredados que continúan visibles. La casilla vuelve a
estar desmarcada cada vez que se abre el diálogo; no altera la configuración
del portafolio origen ni añade un campo al backend. El arreglo nuevo conserva
como efectivos los dos valores enviados.

El diálogo vive en `static/portfolio_repair.js`. Añadir el `<script>` al HTML no
basta: `ManagerHandler` sólo publica los nombres incluidos en `STATIC_FILES`.
La prueba de integración pide el recurso por HTTP y exige respuesta 200; una
prueba que sólo lea el fichero local no detecta este tipo de 404.
