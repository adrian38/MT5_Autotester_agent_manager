# La lectura del HTML nativo no tenía reintento propio (2026-11-22)

## El síntoma

Tres auditorías del mismo turno del cron murieron en el mismo minuto, cada una
en un broker:

```
#105 AXI       MT5_AXI_8: No se identificó de forma unívoca la ventana MT5 de 60082017 en Axi-US50-Live
#185 RoboForex MT5_1: MT5 no abrió el selector Custom period
#2   Vantage   MT5 principal: No se identificó de forma unívoca la ventana MT5 de 34933859 en VantageMarkets-Live 10
```

Las tres bajo el mismo encabezado: «No se pudo obtener el HTML nativo y el uso
corre con una sola terminal, así que no hay ninguna otra donde reintentarlo».
El turno siguiente, cinco minutos después, pasó. Son tropiezos de la
automatización de la GUI, no datos malos.

## Las dos causas

**`_terminal_window` era una foto instantánea.** Todos los pasos vecinos
esperan hasta `timeout` con `_wait` —el Toolbox, el selector Custom period,
Guardar como—; encontrar la ventana, no. Y es el paso con más motivos para
llegar tarde: la auditoría acaba de cambiar el login del terminal, que tarda en
titular su ventana, y `mt5.initialize(path=…)` puede dejar **dos** instancias
del mismo ejecutable vivas un rato (por eso `_export_isolated_native_report`
lleva la cuenta de los PID que lanza). Con el título todavía sin la cuenta y
dos ventanas del mismo `terminal_path`, no hay candidata única y saltaba el
error. El exportador de MT4 (`live_account/mt4_native_history_report.py`), más
nuevo, ya nació esperando: `self._wait(self._ready_terminal_window, …)`.

**`export_native_history_report` sólo reintentaba `_RemoteAllocationError`.**
El bucle de tres pasadas existía desde antes, pero atrapaba una sola excepción;
cualquier otro `NativeHistoryReportError` salía a la primera.

## Lo que se cambió

- `_terminal_window` espera a que la ventana sea identificable, en vueltas de
  0,2 s hasta `timeout`. El escaneo se extrajo a `_terminal_candidates`.
- El error dice **qué vio**: `_window_census` distingue «el terminal no tenía
  ninguna ventana abierta» de «2 ventanas del mismo terminal: …» con sus
  títulos. Un terminal cerrado y dos instancias no se arreglan igual, y el
  mensaje anterior no permitía saber cuál de las dos era.
- El reintento cubre cualquier `NativeHistoryReportError` **mientras el intento
  no haya dejado fichero en el destino**. Esa es la frontera entre «falló la
  GUI» y «falló el informe»: con fichero, repetir sólo cambiaría el error por
  «El destino del reporte MT5 ya existe» y taparía el de verdad.

## Trampas

- **El fichero del manager no lo ejecuta nadie.** Lo que corre es
  `manager_node_runtime/mt5_native_history_report.py` de cada agente, y el
  cambio hay que portarlo allí; además lo importa el proceso del agente
  (`live_audit_extract.py`), así que **exige reiniciarlo**, al revés que
  `backtest/`, que se lanza como subproceso.
- **El módulo estaba clavado en el techo de 600 líneas**, así que el arreglo
  obligó antes a partirlo: las primitivas Win32 —ventanas, controles, diálogos
  y memoria remota— viven ahora en `live_audit/mt5_window_automation.py`, que
  es la capa de abajo y de la que heredan los dos exportadores. El módulo del
  informe reexporta las constantes porque MT4 las pide como `mt5_report.X`.
- ~~**Un uso de una sola terminal no tiene plan B.**~~ **Cerrado el
  2026-11-23.** `_export_isolated_native_report` recorría los *otros* perfiles
  de terminal; con uno solo la lista estaba vacía y el primer tropiezo era
  definitivo. Ahora los candidatos los arma `_native_report_candidates`, que
  pone la **suya** delante de todas cuando su proceso ya no existe: ése es el
  caso que los tres reintentos no pueden arreglar, porque esperan hasta
  `timeout` una ventana que no puede volver. El cuerpo que abre una
  instalación y le pide el HTML se extrajo a `_capture_native_report`, así que
  relanzar la propia y saltar a otra son el mismo código. Con el proceso vivo
  no se relanza, y la captura recuperada se marca
  (`relaunched_capture_terminal`) y lo dice en el log.
- Lo que no se sabe todavía: si en los fallos de AXI y Vantage había cero
  ventanas o dos. El censo del mensaje está puesto justo para contestarlo la
  próxima vez.
