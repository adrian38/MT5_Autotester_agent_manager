# Informes de una estrategia guardada

En el detalle de UBS normal, «Abrir reporte» no debe usar `os.startfile` ni
devolver una ruta local al navegador. El manager entrega el HTML como respuesta
`inline` y la interfaz abre inmediatamente una pestaña vacía desde el clic del
usuario para evitar el bloqueo de ventanas; después carga allí el `Blob`.

«Exportar reportes» descarga un ZIP con todos los HTML que existan para ese
miembro: base, robustez, Final Tick continuo, Final Tick 6M OHLC y Final Tick 6M
every tick. Las cuatro rutas persistidas en la asignación son autoritativas. La
memoria actual del candidato se consulta sólo para recuperar la ruta OHLC 6M,
que el esquema histórico de `portfolio_allocations` no guarda. Esa consulta es
opcional: un SQLite bloqueado o desmontado no puede impedir abrir o exportar los
reportes persistidos que todavía existen en disco.

La pertenencia se valida contra los miembros del portafolio antes de leer ningún
archivo. Todo ocurre en el manager y reutiliza datos de solo lectura; no requiere
cambios en `manager_node_runtime/`.

## El histórico OHLC de un portafolio guardado (2026-10-21)

Petición del usuario: dos botones en el detalle, «Detalle / estrategia» y
«Detalle / portafolio», que abran en otra pestaña lo mismo que enseña
portafolios manuales, con el **histórico OHLC que el portafolio ya tiene**, sin
la comprobación manual. Vive en `mt5_manager/portfolio_history_detail.py` y en
`static/portfolio_history.{html,js,css}`; la ruta es `history-detail`.

**La fuente son dos de los cinco informes del miembro**: `is_report_path`
(base, en muestra) y `oos_report_path` (robustez, fuera de muestra). Medido en
el #62 de ICTrading son contiguos y disjuntos —2020.01→2024.12 y
2025.01→2026.05—, así que juntos son la historia de la estrategia.

- `final_tick_report_path` queda fuera: son seis meses con ticks reales.
- **`full_history_report_path` no sirve aquí**: el tramo continuo no está
  guardado en ninguna cartera de este equipo (0 de 16 en #62, #53 y #50,
  medido el 2026-10-21). Sólo se persiste si cubre todo IS+OOS, y no es el
  caso. Quien lo dé por supuesto se encontrará la pantalla vacía.
- **Las cifras van escaladas por `units`**: los informes se miden todos a lote
  0,01 y un miembro con `units=3` aporta el triple. Lo eligió el usuario; sin
  escalar, el total no sería el de la cartera y contradiría su propio detalle.

### El mensual se cuenta sobre las operaciones, no sobre las transacciones

`StrategyReport.monthly` lo construye MT5 sumando **transacciones** por su
fecha; el beneficio neto sale de las operaciones casadas por su cierre. No es
lo mismo: en el #62 la tabla mensual sumaba 20.053,46 y el neto 18.852,12, las
dos en la misma pantalla. Aquí el mes es el del cierre de la operación, así que
la tabla suma exactamente el profit total. Comprobado en #62, #53 y #50: las
cuatro cifras —total, suma por estrategia, mensual combinado y mensual por
estrategia— coinciden al céntimo.

### Los valles

Un valle va desde que la curva se despega de un máximo hasta que lo recupera;
se enseñan los cinco más hondos. El más hondo coincide con el DD máximo del
portafolio, y eso es lo que valida el algoritmo contra `_drawdown` (comprobado
en las tres carteras).

- **El desglose llega hasta el fondo, no hasta la recuperación.** Con la
  recuperación dentro, lo que subió compensa a lo que bajó: una estrategia que
  perdió 40 y luego ganó 50 aparecía con +10 como causante de un valle de 40.
- La curva se dibuja con una parada por día con operaciones —14.908
  operaciones son 1.652 puntos—, pero el total y los valles se calculan sobre
  cada operación cerrada.
