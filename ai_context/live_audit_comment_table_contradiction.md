# Un comentario que nombra dos setfiles (portafolio #186, 2026-11-01)

El usuario revisó Análisis del uso `audit-186-mvwwcgp8-1` (RoboForex, cartera
#186, 26→31 de octubre) y marcó dos cosas a la vez:

- la suma de PnL de `Gold_Phantom_strat8` y `Gold_Phantom_strat9` no cuadra con
  la de sus filas del detalle de operaciones;
- `Gold_Phantom_strat5` y `Gold_Phantom_strat6` no tienen ninguna real enlazada
  por comentario, aunque el documento ya estaba subido.

Son el mismo fallo visto por los dos lados, y el dato lo decía entero.

## Lo que había guardado

La tabla de `Gamble_comentarios.docx` tenía **14 reglas** para los 16 miembros,
y entre ellas:

```
the gold phantom_xauusd_5 -> Gold_Phantom_strat8
the gold phantom_xauusd_6 -> Gold_Phantom_strat9
```

Ninguna para `_7`, `_8` ni `_9`, que **sí existen** en la cuenta: las 47 reales
del periodo traen `The Gold Phantom_XAUUSD_1` … `_9`. Reproducido en el
scratchpad: un documento con los nueve pares correctos da nueve reglas, y uno
donde varios `.set` repiten el mismo `EA_Comment` da exactamente estas seis, con
`_5` y `_6` apuntando a la **última** fila que los nombra. `rules_from_rows`
guardaba las reglas en un diccionario por comentario, así que la segunda fila
pisaba a la primera sin decir nada.

## Las dos consecuencias

**El importe Real de `strat8` y `strat9` incluía reales ajenas.** Las 3 reales
con comentario `_5` y las 4 con `_6` se atribuían a `strat8`/`strat9`:

| estrategia | emparejadas (detalle) | atribuidas | Real que enseñaba Análisis |
| --- | --- | --- | --- |
| `Gold_Phantom_strat8` | 3 → −13,94 | 3 → −20,03 | **−33,97** |
| `Gold_Phantom_strat9` | 2 → +2,88 | 4 → −15,97 | **−13,09** |

Una real atribuida suma en Real y **no tiene fila en el detalle**, porque
ninguna operación del tester la reclamó. Eso es por diseño; lo que fallaba es
que la fila lo llamaba «atribuidas a mano» y el usuario sabía que él no había
atribuido nada, así que descartaba la única explicación que había.

**Y `strat5`/`strat6` se quedaban sin real por la misma tabla.** `_may_pair`
cierra la puerta a cualquier estrategia que no sea el dueño declarado, así que
las seis filas del tester de `manual:5` y `manual:6` salían `SIN REAL`
—con su real libre, del mismo símbolo, lado y lote, **a 0 segundos**— y el
motivo publicado era `open_time_outside_tolerance`. La pantalla remataba:
«Había una real libre más cercana que no se consumió por 0 s: el fallo puede
ser del emparejamiento y no de la cuenta». Mandaba a mirar justo donde no
estaba.

Con la tabla corregida (`_N → stratN`), medido sobre el mismo payload: **0
filas sin real** en las 36, `strat8` vuelve a −13,94, `strat9` a +2,88, y
`strat5`/`strat6` emparejan 2 y 4. La única atribuida que queda es la `_4` de
las 09:48:50, que no tiene operación del tester que la reclame.

## Lo que se cambió

- `rules_from_rows` **rechaza** el documento cuando un comentario nombra dos
  `.set` distintos, y dice cuáles. Repetir la fila entera no es contradicción.
  Un comentario que nombra dos `.set` no identifica a ninguno: mejor no
  importar la tabla que importarla torcida.
- `_missing_comparison` publica `nearest_unused_real_owner` y el motivo
  `claimed_by_comment_owner` cuando la real libre más cercana la reclama otra
  estrategia. Las dos pantallas lo leen y nombran a esa estrategia, que es lo
  único que hay que corregir en el documento.
- La fila de Análisis dice de dónde salió cada atribución —comentario o
  Excel— y que esas operaciones no tienen fila en el detalle.

## Lo que conviene recordar

- **Un diccionario construido en un bucle es una fusión silenciosa.** Aquí la
  llave era el comentario y el valor el `.set`; quien escribió el documento no
  tenía forma de ver que tres de sus filas no habían llegado. El contador de la
  pantalla decía «14 setfiles» y 14 reglas había.
- **El motivo de una fila sin pareja tiene que salir de por qué no se emparejó,
  no de lo que se midió.** `_missing_comparison` elegía el candidato más
  cercano sin pasar por `_may_pair`, así que describía una tolerancia que nadie
  había comprobado. Un `delta=0.0` junto a «fuera de tolerancia» es la señal.
- El nodo no entra aquí: la tabla de comentarios vive en el perfil del manager
  y `manager_node_runtime/live_audit_compare.py` no la conoce. Su
  `_missing_comparison` sigue como estaba, y es correcto que lo siga.

Ver [live_audit_comment_links.md](live_audit_comment_links.md) para el contrato
de la tabla y [live_audit_pairing_20261026.md](live_audit_pairing_20261026.md)
para lo que el comentario **no** decide.
