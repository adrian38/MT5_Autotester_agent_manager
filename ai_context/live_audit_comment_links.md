# Enlazar las reales por el comentario del EA (2026-10-22)

El usuario llegó con el emparejamiento automático por atribución recién hecho y
una frase corta: «no funciona tan bien como esperaba». Para Tesla y **sólo en
AXI** hay una prueba mejor que el tiempo, el lote y el Excel: el EA escribe en
el comentario de la orden de apertura un código —`TESLA_M15_006`— que nombra el
`.set` que la abrió. No es una conjetura del auditor ni una opinión escrita a
mano después: lo escribió el programa que operó.

El comentario no dice el nombre del `.set`, así que el usuario mantiene aparte
un `.docx` de dos columnas, «Nombre del setfile» y «EA_Comment», con las once
estrategias de Tesla. Se sube desde Análisis y se guarda en el perfil del uso,
como el Excel del autolote y el de la atribución.

## Lo que midió la decisión

Sobre la materia prima de `audit-109` (`Christmas_Axi`), inyectando los
comentarios desde el HTML nativo que el agente ya guardaba:

| Escenario | SIN REAL | Reales sueltas | Parejas automáticas |
| --- | --- | --- | --- |
| El Excel que el usuario mantenía a mano | 6 | 11 | 3 |
| Ni Excel ni tabla | 8 | 14 | 0 |
| **Sólo la tabla** | **6** | **11** | **3** |

La tabla sola reproduce exactamente lo que costó repartir a mano, y las once
reales sueltas pasan a quedar atribuidas con origen `comment`. **No descubre
operaciones nuevas**: lo que ahorra es el trabajo. Las seis filas `WTI.fs` que
siguen «SIN REAL» no las toca ninguna de las dos cosas —no hay reales de esas
estrategias en el periodo.

## No es un camino nuevo de emparejado

Ésa es la decisión de diseño que lo hace barato. `attribution_from_comments`
resuelve a la **misma forma** que el Excel —`ticket → estrategia`— y entra por
la misma puerta, `_real_attribution_by_ticket`. Así, sin tocar nada más:

- anota la real que ningún cierre del tester reclamó (`annotate_unmatched`);
- filtra lo que ofrece el selector «Emparejar a mano» (`offerableTo`);
- y forma la pareja sola cuando la estrategia tiene además una fila «SIN REAL»
  (`attributed_assignment`, de [live_audit_manual_pairs.md](live_audit_manual_pairs.md)).

La fila distingue las dos pruebas: `attributed_pair` vale `"comment"` o
`"manual"`, y el texto no las presenta igual. Un desacuerdo mudo entre «lo
escribió el EA» y «lo escribió el usuario» sería lo peor que podría pasar aquí.

## El comentario manda sobre el Excel

Decidido con el usuario, que eligió entre las tres opciones. Lo escribió el
programa que operó; el Excel lo tecleó él cuando no tenía esa señal. Así, subir
el documento retira solo las atribuciones viejas que lo contradigan, sin tener
que ir a quitarlas una por una —que era justo la pega de la opción contraria.

### Salvo sobre una exclusion (2026-10-22, el mismo dia)

«Manda sobre el Excel» se aplico entero y se llevo por delante lo que no debia.
El usuario subio `auditor_18.10.2026_no_contabiliza.xlsx` marcando 15 reales
como «no contabiliza en este portafolio», y la tabla las devolvio a la curva
Real atribuidas a `manual:15`: **−31,01 €** que el habia sacado a mano,
reaparecidos sin que nada lo dijera. Lo vio el, no una prueba —y habia una
prueba fijando justo el comportamiento equivocado—.

Las dos cosas que el Excel puede decir de un ticket no son del mismo tipo:

- «es de esta estrategia» es una conjetura sobre el **dueno**, y ahi el
  comentario sabe mas: lo escribio el programa que abrio la orden.
- «no contabiliza en este portafolio» es una afirmacion sobre **pertenencia**,
  y el comentario no esta en condiciones de contradecirla: solo nombra el
  `.set`, no el portafolio. La misma estrategia puede estar operando fuera de
  este portafolio, y entonces su comentario es correcto y la operacion sigue
  sin contar aqui.

Y una exclusion no siempre es deliberada: `attribution_by_ticket` marca igual
lo que nombra un `.set` que ya **no es miembro**, y eso si es una atribucion
vieja, justo lo que subir el documento tenia que poder corregir. Se distinguen
por el texto con `_names_a_strategy`, el mismo criterio que ya usa el
importador para decidir si una celda nombra una estrategia: una frase manda
sobre el comentario; un nombre de `.set` que nadie reclama, no.

Por encima de las dos siguen las **parejas del selector**: ésas las decide el
usuario mirando la fila, y se consumen antes que cualquier coste.

Y nada de lo automático se guarda: el análisis se recalcula entero al leer, así
que ninguna decisión anterior sobrevive ni bloquea a la siguiente. Lo único
persistido es lo que el usuario sube o elige.

## Sólo AXI, y se comprueba en la puerta

`comment_links_supported` mira el broker del nodo, y **el campo es
`portfolio_broker`**: es el que lleva el registro de `manager.json`. El
`broker` a secas sólo aparece en lo que el agente dice de sí mismo —con él
compone `/api/nodes` su respuesta— y no llega hasta aquí. Mirar sólo a `broker`
dejó el botón oculto también en AXI y **en silencio**: una capacidad que no se
anuncia no se echa de menos, y sólo se vio porque el usuario preguntó dónde
estaba el botón. Se aceptan los dos campos porque las dos formas circulan.

En los demás brokers el comentario
trae lo que ponga el terminal —`[sl 359.87]`, el nombre de la plataforma— y
leerlo como si nombrara una estrategia enlazaría reales ajenas sin que nada lo
dijera. Se comprueba en la ruta, no en el análisis: así ningún otro uso llega
siquiera a tener la tabla guardada. La pantalla recibe la capacidad en
`analysis.comment_links_supported` y el botón nace `hidden`.

El `.set` lo decide la **celda, no la columna**. Ordenar por columna rompería
con sólo invertirlas y el error sería silencioso: cada comentario quedaría
atado a un `.set` que no existe y la tabla diría que no enlaza nada. La celda
que acaba en `.set` se reconoce sola.

Un `.set` que no esté en el portafolio del uso **se descarta**, no se marca
como ajeno: la tabla es una sola para todos los usos y nombrar el `.set` de
otro portafolio no dice nada de éste.

## Hacía falta tocar el nodo

El comentario no estaba en los datos. `_real_trades` lo usaba sólo como
suplente de `strategy` cuando el magic es 0 —por eso el `USDJPY` de `p_xmas`
salía con el comentario por estrategia y los Tesla con su magic— y nunca lo
publicaba. Se añadió `"comment"` a la operación real y a la posición abierta,
tomado siempre de la **orden de apertura**: la de cierre lleva el marcador del
SL/TP.

Eso vive en `manager_node_runtime/live_audit_extract.py` de cada agente, no
aquí: tres commits hermanos (IC, AXI, RoboForex/`dev`), el espejo del manager
en `live_audit_extraction.py`, y FUTURE omitido por congelación.

**Las auditorías ejecutadas antes de ese reinicio traen las reales sin
`comment` y la tabla no tiene nada que leer.** No se avisa de ello: una tabla
sin comentarios que casen es indistinguible de un uso que no es de AXI. Para
aprovecharla hay que reiniciar el agente y volver a ejecutar el uso.

### Y `_trade_view` es la lista blanca que lo tira

Anadirlo al extractor no bastaba. `_trade_view` del agente —`live_audit_helpers.py`—
filtra por una lista de campos y es **lo ultimo que toca la operacion** antes de
publicarse en `last_payload`. Con el extractor ya arreglado, el agente reiniciado y la
auditoria repetida, las 127 operaciones llegaron sin `comment`.

No lo delato ninguna prueba: el extractor era correcto, el manager sabia leer el campo,
y el unico sitio donde se perdia no lo miraba nadie. Lo caza ahora
`tests/node/test_node_trade_view_parity.py`, que compara **el conjunto de campos** del
`_trade_view` del manager con el de cada copia montada: anadir uno en un solo lado
rompe. Comprobado quitando `comment` de la copia de AXI.

Lo aviso el usuario dos veces —«si no agregas la columna de comentario no creo que se
guarde»— antes de que yo lo comprobara. La leccion util: cuando un campo nuevo tiene que
cruzar el limite nodo→manager, el camino son **tres** sitios (extractor, serializador y
lector), no dos.

El manager no puede sacarlo de otro sitio: el HTML nativo sí trae la columna
—en UTF-16, en un `td class="hidden"`— pero se queda en el disco del agente y
nunca sube al manager.
