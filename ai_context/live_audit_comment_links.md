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

Por encima de las dos siguen las **parejas del selector**: ésas las decide el
usuario mirando la fila, y se consumen antes que cualquier coste.

Y nada de lo automático se guarda: el análisis se recalcula entero al leer, así
que ninguna decisión anterior sobrevive ni bloquea a la siguiente. Lo único
persistido es lo que el usuario sube o elige.

## Sólo AXI, y se comprueba en la puerta

`comment_links_supported` mira el broker del nodo. En los demás el comentario
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

El manager no puede sacarlo de otro sitio: el HTML nativo sí trae la columna
—en UTF-16, en un `td class="hidden"`— pero se queda en el disco del agente y
nunca sube al manager.
