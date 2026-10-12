# Un uso NO COMPARABLE se leía como «COINCIDE» (AXI, 2026-10-12)

## El síntoma

`audit-105-mv4perus-1` (AXI, cuenta 60082017, periodo 05/10→11/10) abría la
pantalla de resultado con el badge verde **COINCIDE**, «No se detectaron
desviaciones», «25 de 25 cierres» de pertenencia al modo y todos los contadores
a cero. El JSON guardado decía otra cosa:

```
status: not_comparable · NO COMPARABLE
summary: History Quality 66.00% inferior al mínimo 80.00%
real 25 · tester 34 · quality 66.0
```

## La cadena, de abajo arriba

1. **Journal del terminal** (`Terminal\<hash>\logs\20261012.log`):
   `2 05:47:28.536 Ticks XAUUSD: received too many containers without changes [51]`.
   MT5 abandona la descarga de ticks y deja la caché a medias.
2. **Log del agente de tester**: `XAUUSD: history ticks synchronized from
   2026.10.07 to 2026.10.09` y `real ticks begin from 2026.10.07`, con el test
   corriendo `from 2026.10.05 00:00 to 2026.10.11 00:00`. Cuatro días de seis:
   **66 %**.
3. **No es del broker**: el mismo símbolo en el terminal `6FBEE76C` tenía ticks
   desde `2026.08.31` y su set salió al 100 %. Sólo los dos XAUUSD H1 que
   cayeron en `E1589DCF` y `E8A92A24` dieron 66 %; los otros once sets, 100 %.
   **Qué worker te toca decide si la auditoría vale.**
4. **`quality = min(qualities)`** (`live_audit_analysis.py`): 66 < 80 →
   `not_comparable` y `_compare` no se ejecuta. El resultado se guarda sin
   `within_tolerance_trades`, `open_real_trades`, `missing_real_trades`,
   `extra_real_trades`, `deviating_pairs` ni `comparison_detail`.
5. **La pantalla los leía como ceros.** `needsReview` salía 0 y el badge caía en
   `COINCIDE`; la pertenencia al modo la pintaba de verde el fallback
   `portfolio_closures ?? real`. Una auditoría que no midió nada se leía como la
   que cuadra.

La prueba de que la comparación no corrió está en el propio cuadro: 25 cierres
reales y 0 emparejados. Si `_compare` hubiera corrido con 25 reales y 0 del
tester, los 25 habrían salido como `extra_real_trades`.

## Lo que se cambió

- **Casilla «Usar el multiterminal del nodo»** por uso, **desmarcada por
  defecto**. Desmarcada, el uso entero pasa por la terminal principal (la
  primera habilitada del broker activo): misma caché de ticks para todos los
  sets. El recorte se aplica dentro de `_terminal_profiles`, no en el pool del
  tester, porque la cuenta real, el informe nativo y la restauración preguntan
  por su cuenta y los cuatro tienen que ver la misma terminal.
- **El resultado ya no miente**: badge `NO COMPARABLE`, veredicto propio con la
  calidad obtenida contra el mínimo, la lista de sets que no la acreditan y la
  tabla por estrategia mostrando la calidad de cada uno.
- **`finalizada`** usa `analysis.executed_at`: `completed_at` se vuelve a sellar
  en **cada lectura** (`_result_base` llama a `utc_now()` y el manager analiza
  al leer), así que la pantalla enseñaba la hora de abrir la página. Tres
  auditorías de días distintos devolvían la misma.

## Trampas

- El auditor **lo ejecuta el agente**, no el manager: `manager_node_runtime/
  live_audit_*.py`, con los ficheros renombrados (`live_audit_compare.py` aquí
  es `live_audit_comparison.py` allí). El manager sólo analiza la materia prima
  al leerla (`analysed_by: manager`), que es lo que permite arreglar
  retroactivamente auditorías ya guardadas sin relanzarlas.
- Los logs de MT5 son **UTF-16LE**: `rg` y `grep` no encuentran nada en ellos.
- `[Multiterminal] enabled=false` en el `ui_settings.ini` del agente ya daba un
  worker, pero es global del nodo y no adapta el resto del camino.

## La calidad marca, ya no anula

Anular la auditoría entera era lo peor de los dos mundos: la calidad la decide
el historial de ticks de la terminal que ejecutó **ese** set, no la cuenta
auditada, así que once sets al 100 % se quedaban sin medir por culpa de dos.

Ahora `_compare` corre siempre y `_mark_quality` añade al resultado
`minimum_tick_history_quality_pct` y `strategies_below_quality`, y marca cada
fila de `strategy_summary` con `history_quality_pct` y `quality_ok`. La
pantalla pinta esa estrategia en rojo —`CALIDAD INSUFICIENTE`— y avisa arriba
(`REVISAR LA CALIDAD TICK`), pero el resto del veredicto vale.

Como el veredicto lo pone el manager **al leer**, el uso 105 pasó a comparar
sin relanzar nada: de «0 de todo» a 26 parejas alineadas, 13 correctas, 1 real
abierta y los dos XAUUSD marcados.

`not_comparable` ya no lo produce el manager. Se mantiene en `STATUS_LABELS` y
en la pantalla porque un agente sin portar sigue pudiendo decidirlo él
(`has_raw_material` falso: el manager muestra su resultado tal cual).
