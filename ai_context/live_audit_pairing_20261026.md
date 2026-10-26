# La huella de SL/TP no puede ganarle a un día de distancia (2026-10-26)

El usuario llegó con la tabla del auditor de `audit-109` (`Christmas_Axi`) y
filas de Tesla+ visiblemente cruzadas. La puerta del comentario del EA
—[live_audit_comment_links.md](live_audit_comment_links.md)— ya estaba puesta y
funcionando: ninguna real acababa en una estrategia distinta de la que nombra su
comentario. El cruce era **dentro** de la estrategia, que es lo único que el
comentario no puede decidir.

En `tsv01` las once reales llevan todas `TESLA_M15_006`, así que el documento
dice «son de `tsv01`» y calla sobre qué fila le toca a cada una:

```
T25  tester 21/10 17:15 -> 22/10 16:30   real #47266032  22/10 17:15 -> 22/10 18:10   <- 24 h
T27  tester 22/10 17:15 -> 22/10 18:09   real #47193136  21/10 17:15 -> 22/10 16:30   <- 24 h
```

`#47193136` coincide **clavado** en apertura y cierre con `T25`, y `#47266032`
en apertura con `T27`. El auditor eligió el cruce de un día entero.

## Por qué

`_pair_cost` ordenaba `(huella, lote, apertura_fuera, cierre_fuera, distancia)`.
Con el límite de precio en 0,15 absolutos:

| Pareja | ΔSL | ΔTP | Huella |
| --- | --- | --- | --- |
| `T25` ← `#47266032` (abre 24 h después) | 0,03 | 0,03 | las dos dentro → 0 |
| `T25` ← `#47193136` (clavada) | 0,15 | 0,50 | una fuera → 2 |

La huella iba primero, así que ganaba la del día siguiente. Y entraba en el
reparto por la cláusula que rescata una pareja con apertura *y* cierre fuera de
tolerancia cuando los stops completos coinciden y el lote cuadra.

**La misma estrategia repite niveles casi idénticos de un día para otro**
(381,69/399,55 contra 381,51/400,02). Entre dos candidatas de días distintos la
huella no es evidencia: es ruido con tres decimales.

## El criterio ahora

`(apertura_fuera, cierre_fuera, huella, lote, distancia)`. El tiempo decide
primero; la huella sigue desempatando entre las que caen dentro de tolerancia,
que es para lo que se puso —los dos XAUUSD del 2026-10-04 que abren en el mismo
segundo siguen resolviéndose por ella.

Medido sobre el payload real de `audit-109`: las parejas con apertura fuera de
tolerancia bajan de **4 a 1**, y la que queda (`T54` de `tsv12`, a 23,5 h) es el
sobrante real de esa estrategia, no una elección entre dos.

## Dos cosas que conviene no olvidar

- **La puerta del comentario no se tocó.** Sigue siendo un `return None` antes
  de calcular ningún coste: si el comentario nombra otra estrategia, no hay
  pareja posible. Este orden sólo decide **la fila dentro de la estrategia que
  el comentario ya autorizó**, que es terreno donde el comentario no habla.
- **Sin niveles declarados ahora manda el cierre, no el lote esperado.** Es el
  caso ciego de `test_one_level_apart_is_better_evidence_than_both_apart`, que
  fijaba lo contrario. Va en la misma dirección que la lección que lo escribió:
  el lote con autolote es una simulación y el cierre un hecho observado.

Lo fija `tests/live_audit/test_live_audit_assignment.py::
test_the_footprint_does_not_beat_a_day_of_distance`.
