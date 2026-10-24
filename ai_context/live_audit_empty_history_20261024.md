# «0 reales» con 50 operaciones en el HTML (RoboForex, 2026-10-24)

## El síntoma

`audit-170-mvdscovr-2`, ejecución `20261025_003002_773621`, portafolio #170
(«Arreglo del portafolio #148», variante agresiva), cuenta 77049426, periodo
18→24/10. La pantalla daba **REQUIERE REVISIÓN** con:

```
Coincidencia parcial: 0 cerradas correctas, 0 reales abiertas y 48 sin real
Pertenencia al modo  0 de 0 cierres     Sin pareja  48 tester · 0 reales
```

El usuario tenía delante el HTML de la cuenta y sí veía las operaciones.

## Lo que decía el log del uso

```
Cuenta MT5 verificada: login 77049426 ... 0 deals brutos, 0 cierres y 0 apertura(s)
anterior(es) recuperada(s) tras 6 consulta(s). HTML nativo real_account_mt5_report.html
capturado por MT5_1 con periodo custom 2026-10-18 a 2026-10-25, 303846 bytes
0 cierres reales reconstruidos antes del filtro del portafolio.
```

**El filtro de banda de lote no tuvo nada que ver** —fue la primera hipótesis y
era falsa—: `comparison_detail.discarded_real_operations` estaba vacío y
`unmatched_real` también. La lista llegaba vacía desde MT5.

El culpable es `mt5.history_deals_get(period_start, period_end)` dentro de
`_synchronised_history`: devuelve la caché local del terminal, que puede estar
vacía justo después de cambiar de login. Reintenta `history_sync_attempts = 6`
veces con `history_sync_delay_seconds = 1.0`, o sea **~5 segundos**, y si
siempre vuelve vacía no es error: sólo `None` levanta excepción, una lista
vacía se acepta y la auditoría publica que la cuenta no operó.

## La prueba de que sí había operaciones

El mismo terminal, en la misma ejecución, exportó
`reports/real_account_mt5_report.html` (UTF-16LE, como los journals de MT5).
Pasándole el lector nuevo:

```
cerradas en periodo: 50    abiertas al cierre del periodo: 14
```

con símbolos que son exactamente los del portafolio auditado. El HTML se
exporta pilotando la GUI (History → Custom period → Guardar informe), que
fuerza su propia sincronización; por eso ve lo que la API no veía.

## Por qué no se usaba ese HTML

Porque era **prueba, no fuente**. `validate_native_history_report` sólo
comprobaba la firma del terminal, el login y que pesara más de 512 bytes, y de
ahí sólo viajaban `filename`, `bytes`, `sha256` y las fechas hasta la pantalla.
Las operaciones salían únicamente de la API, y nadie contrastaba las dos cosas:
110 transacciones en el archivo contra 0 de la API pasaba en silencio.

Lo irónico: `live_audit_autolot.balance_history_from_html` ya llamaba a
`parse_report()` sobre **ese mismo archivo**, pero sólo para sacar la columna de
saldo del autolote, y tiraba el resto.

## Lo que se cambió

- **`portfolio_manager/mt5_account_history.py`**, nuevo y byte a byte idéntico
  en los cuatro checkouts: lee las secciones Posiciones, Transacciones y
  Posiciones abiertas del informe nativo. No es `mt5_report.py`, que lee el
  reporte del Strategy Tester.
- **`_contrasted_with_native_report`** en `live_audit_extraction.py` (manager) y
  en `manager_node_runtime/live_audit_extract.py` (agentes), llamada desde
  `_extract_real` justo después de exportar el HTML.

El criterio, a propósito estrecho:

| API | informe | qué pasa |
| --- | --- | --- |
| trae operaciones | lo que sea | **no se toca nada**; sólo se anota `native_closed_positions` |
| vacía | con operaciones | mandan las del informe; `real_trades_source = native_terminal_report` |
| vacía | vacío | la cuenta no operó, resultado vacío legítimo |
| vacía | ilegible | `RuntimeError`: antes que publicar un «0 reales» falso |

**Una discrepancia parcial no cambia nada y es deliberado**: el informe se pide
por fechas, sin hora (`period_start.astimezone().date()`), así que puede traer
cierres de fuera de la ventana con toda legitimidad. Sólo el caso catastrófico
—la API a cero— cambia de fuente, y por eso ninguna auditoría que hoy funcione
puede cambiar de resultado.

## Trampas del lector

- **Las dos tablas de posiciones se leen por POSICIÓN, no por nombre de
  columna.** Repiten «Fecha/Hora» y «Precio» para apertura y cierre: un
  `dict(zip(cabecera, celdas))` se queda sólo con el cierre y pierde la
  apertura. El orden de columnas lo fija el terminal y no cambia con el idioma;
  las etiquetas sí.
- **MT5 mete una celda oculta** (`class="hidden" colspan="8"`) entre el tipo y
  el volumen. Sin descartarla, todo se lee corrido desde el volumen.
- Los importes llevan el millar separado por espacio: `2 319.95`.
- Las horas del informe son las del servidor y se etiquetan UTC **sin
  convertir**, que es lo que ya hace el camino de la API con `deal.time` y lo
  que hacen los reportes del tester. Convertirlas rompería el emparejado.
- **El comentario del EA no siempre se puede recuperar.** La tabla de
  posiciones no lo trae y las transacciones no dicen a qué posición pertenecen:
  se casan por símbolo y hora exacta de apertura. Una posición abierta antes del
  periodo no tiene su transacción en el informe y se queda sin comentario. En
  AXI eso es el nombre del `.set`, así que una auditoría salvada por el HTML
  tiene menos huella que una normal.
- El informe sí da gratis dos cosas que la API cuesta: el **SL/TP de entrada**
  (sin `history_orders_get` por posición) y las **aperturas anteriores al
  periodo**, que `_recover_prior_openings` reconstruye con consultas extra.

## Lo que queda abierto

Esto es una red, no la cura. **Nadie sabe todavía por qué el terminal devolvió
la caché vacía** en esa ejecución: 6 consultas en 5 segundos pueden ser pocas
para una ventana de una semana justo tras conmutar el login. Subir
`history_sync_attempts` o el retardo es otra unidad, y ahora el log lo delata:
cuando la red salta, la línea dice «MT5 no devolvió ningún deal, así que las
operaciones reales salen del informe nativo del terminal».
