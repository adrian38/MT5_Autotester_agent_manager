# La auditoría larga pidió un periodo vacío y MT5 ni arrancó el tester

Síntoma, el 2026-11-02 a las 02:27 en la campana, portafolio #185 de RoboForex
ECN, repetido ocho veces en el mismo aviso:

```
Strategy Tester terminó con código 1: ERROR: No se encontro ningun reporte
generado para este backtest. DIAG WORKER_JOB_DONE profile=MT5_1 job=1
exit_code=1 failures=1 ... job=8 exit_code=1 failures=8
```

El mensaje señala al EA, al símbolo o al histórico, y no era ninguno de los
tres: cuatro horas antes, a las 21:52 del 31-10, ese mismo terminal, ese mismo
EA y ese mismo símbolo habían corrido los ocho trabajos con código 0.

## Lo único que cambiaba eran las fechas

El `.ini` que el runner escribe queda copiado entero en `runner.log`. El del
intento fallido, junto al que sí funcionó:

| | 31-10 21:52 (bien) | 02-11 02:22 (mal) |
| --- | --- | --- |
| `FromDate` | `2026.10.22` | `2026.10.31` |
| `ToDate` | `2026.10.31` | `2026.10.31` |

Todo lo demás —`Expert`, `Symbol=XAUUSD`, `Period=D1`, `Model=4`, depósito,
apalancamiento— es idéntico.

El journal principal del terminal dice lo que el runner no sabe decir:

```
Network  '27505044': authorized on RoboForex-Pro ... terminal synchronized
Terminal tester didn't start
Terminal shutdown with -1000012351 (tester has not been started)
```

Siete segundos por intento, dos intentos por trabajo, ocho trabajos. El
terminal arranca, se conecta, se sincroniza y se niega a arrancar el tester con
un rango vacío.

## Por qué el manager pidió un día

`live_audit_full_period.audit_period` arma el tramo de la larga con el final en
la **fecha de la cita** —no en «hoy», a propósito, para que recuperar una cita
vencida no mueva la ventana— y el arranque en el corte largo anterior. Sin
corte anterior baja a `oldest_active_start` y después lo levanta la fecha de
creación del uso, que es el suelo: antes de existir el portafolio no hay
operaciones reales con las que comparar.

Aquí los dos extremos cayeron en el mismo día:

```
audit-185-mvwv7msg-1   creado el 2026-10-31 20:40 UTC
cita                   2026-10-31T23:00:00+01:00  →  2026-10-31
```

El uso nació **el mismo sábado de la cita**. El único candado que había era
`if start_date > end_date`, y `start == end` lo atravesaba.

Y ese único día es siempre un sábado, porque el final de una larga es por
definición el día de la cita semanal. Mercado cerrado: aunque MT5 aceptara el
rango, no habría nada que comparar.

## Arreglo

`audit_period` se partió en tres piezas, porque el diccionario vacío confundía
dos cosas distintas:

| | Antes | Ahora |
| --- | --- | --- |
| No se puede fijar la ventana | `{}` | `audit_window` → `None` |
| La ventana existe pero está vacía | `{}` **y se lanzaba igual** | `has_days` → `False` |

Eso importaba porque `_launch_payload` interpreta el `{}` como «no hay periodo
largo» y cae en `chained_period`, que termina en *hoy*: la cita se lanzaba de
todos modos, sólo que con otra ventana.

Con la ventana vacía, `live_audit_full_period.close_empty_turn` da el turno por
cumplido sin tocar el agente: apunta `last_full_audit_end` —igual que
`completion_changes` cuando la larga sí corre, así que la larga siguiente
arranca donde habría terminado ésta y no se pierde ningún día— y deja un aviso
`info` de clase `full_audit_empty` en la campana.

Lo comprueba
`tests/live_audit/test_live_audit_cron_watch.py::test_a_long_audit_born_on_the_day_of_the_appointment_is_not_launched`,
que además exige que la vuelta siguiente no reintente ni repita el aviso.

## Lo que queda pendiente

**El runner de los agentes traduce mal este código.** `MT5_TESTER_ABORT_CODES`
de `run_tests_base.py` sólo conoce `-1000012358` y `-1000012362`, así que
`-1000012351` cae por el camino de «no apareció el reporte»: gasta un reintento
inútil por trabajo y escribe un mensaje que señala al EA y al histórico en vez
de decir que el tester no arrancó. Añadirlo con `transient=False` es un commit
hermano por checkout (IC, AXI, RoboForex/`dev` y Vantage; FUTURE congelado).

**El encadenado semanal tiene la misma forma, pero con otro final.** Ahí la
ventana termina en *hoy* y el último día se vuelve a cubrir a propósito, así
que `start == end` sólo sale si la auditoría anterior terminó hoy mismo. No se
ha tocado: el criterio de la larga no vale tal cual para el semanal.
