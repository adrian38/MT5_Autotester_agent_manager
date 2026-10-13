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
4. Se reconstruye el mismo número de estrategias activas que tenía esa
   variante. Las plazas vacías se cubren sólo con candidatos que pasan las
   cuatro etapas aceptadas y los filtros guardados: grupos, símbolos
   deshabilitados, Grid OFF, tres meses positivos, uso previo y aporte Final
   Tick 6M.
5. Si no existe una composición completa válida, no se ofrece un resultado
   parcial.

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

## El refinamiento profundo también respeta el tamaño (2026-10-13)

El greedy ya recibía `minimum_active_strategies` y
`maximum_active_strategies` con el tamaño original, pero el refinamiento
profundo posterior sólo recibía el mínimo. Con «Optimización profunda» activa
podía añadir candidatas nuevas después de alcanzar el cupo: una reparación de
16 terminó con 19 y la validación final la rechazó con «No se pudo reconstruir
el tamaño original».

`_deep_refine_allocations` propaga ahora también el máximo. Los movimientos de
adición pueden aumentar unidades de una estrategia ya activa, pero no activar
otra al alcanzar el cupo; los intercambios siguen permitidos porque conservan
la amplitud. Es un límite general del optimizador full-history, no una regla
especial de la pantalla de reparación. El refinamiento mensual estricto usa su
propio motor y no cambia.

## Exclusión mutua entre cálculos

Un nodo sólo puede tener un cálculo de portafolio activo, aunque el segundo
inicio llegue desde otro scope (UBS, mensual o Grid). El segundo se rechaza sin
tocar el evento de cancelación ni el trabajo en curso y la API responde que el
generador está ocupado y debe intentarse cuando termine. En la pantalla actual
también quedan deshabilitados Generar, Mejorar y Arreglar mientras haya una
operación activa. Iniciar otra generación nunca equivale a detener la anterior.
