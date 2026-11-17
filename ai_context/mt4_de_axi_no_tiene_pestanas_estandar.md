# El MT4 de Axi no tiene pestañas estándar, y su menú exige el botón

Lo que lee el historial de una cuenta MT4 es
`mt5_manager/live_account/mt4_native_history_report.py`, bifurcado en
`manager_node_runtime/mt4_native_history_report.py` de AXI, que es **el que
corre**. De los agentes montados sólo AXI tiene ese módulo: IC, RoboForex y
Vantage leen MT5 y no lo llevan.

Diagnosticado el 2026-11-17 contra la terminal real de Axi (cuenta 3083784,
`C:\Program Files (x86)\MetaTrader 4 Axi Terminal\terminal.exe`), después de
que el sondeo muriera en «No se encontró Account History».

## Fallo 1: el panel Terminal no monta un `SysTabControl32`

La ruta que funciona en MT5 —buscar un `SysTabControl32`, preguntarle sus
etiquetas con `TCM_GETITEMW` y pulsar la que diga «Account History»— aquí no
encuentra nada. Este MT4 dibuja «Operaciones / Exposición / Historial de
cuentas / …» en una franja MFC propia (`AfxWnd140s`), sin texto que preguntar:
`GetWindowTextW` devuelve cadena vacía y no responde a los mensajes `TCM_*`.

Lo que sí es estable es el **id del panel que cada pestaña destapa**. El árbol
real, bajo el dock `AfxControlBar140s` titulado «Terminal» y su contenedor
`Afx:008B0000…`, también titulado «Terminal»:

| x de la franja | Panel | `GetDlgCtrlID` | Pestaña |
| --- | --- | --- | --- |
| 4–87 | `SysListView32` | 33217 | Operaciones |
| 88–165 | `#32770` | 38311 | Exposición |
| **166–291** | `SysListView32` | **33208** | **Historial de cuentas** |
| 292–363 | `SysListView32` | 33211 | Noticias |
| 364–423 | `SysListView32` | 33206 | Alertas |
| 424–531 | `SysListView32` | 33210 | Buzón |
| 532–603 | `#32770` | 4081 | Mercado |
| 604–657 | `#32770` | 1405 | Señales |
| 658–729 | `SysListView32` | 33216 | Artículos |
| 730–801 | `SysListView32` | 33212 | Biblioteca |
| 802–885 | `SysListView32` | 35434 | Expertos |
| 886– | `SysListView32` | 33209 | Diario |

Por eso `_custom_history_tab` busca el panel 33208 y pulsa la franja de
izquierda a derecha hasta que ese panel queda visible; si ya lo está, no pulsa
nada. **La x no se escribe en el código**: el primer intento clavaba `x=220`
porque ahí cae el tercer tab en esta instalación, y eso se rompe con otro
idioma o con otra fuente, que cambian el ancho de cada etiqueta.

Las x de la tabla están en **coordenadas de cliente de la franja**, que son las
mismas unidades que devuelve `GetWindowRect`: no hay que escalar por DPI, y
escalar rompía el clic.

## Fallo 2: el menú contextual sólo abre si el wparam declara el botón

Con la pestaña ya seleccionada y el control correcto debajo, seguía fallando en
«MT4 no abrió el menú de Account History». La causa no es la posición ni el
foco ni la cabecera de columnas: el `WM_RBUTTONDOWN` tiene que llevar
`MK_RBUTTON` (0x0002) en su **wparam**. Con 0, MT4 se traga el clic y no pasa
nada. Comprobado aislando las dos variables:

```
x=12 y=36 wparam=0 -> nada
x=12 y=36 wparam=2 -> POPUP
x=60 y=36 wparam=0 -> nada
x=12 y=12 wparam=2 -> POPUP
```

La ventana **no** necesita estar en primer plano ni tener el foco, y la posición
da igual, cabecera incluida.

El menú que sale trae, en español, `Todo el historial`, `Últimos 3 meses`,
`Último mes`, `Período personalizado...` (35432), `Guardar como informe` (33064)
y `Guardar como informe detallado` (35502). Son los que busca `_context_command`
por texto.

## Cómo volver a diagnosticarlo

Enumerar el árbol con `EnumChildWindows` imprimiendo clase, rect, visibilidad e
id de cada descendiente; y para mapear la franja, pulsarla cada pocos píxeles
anotando qué hijo del contenedor queda visible. Eso da la tabla de arriba en una
pasada y sin tocar nada de trading.

Ojo con `_children`: usa `EnumChildWindows`, que devuelve **todos los
descendientes**, no los hijos directos. Para quedarse con los hijos del
contenedor hay que filtrar por `GetParent`.

## Validación

La exportación completa se puede probar sin pasar por el agente, llamando a
`export_native_mt4_history_report` desde la copia del runtime de AXI. El 2026-11-17
devolvió un HTML nativo de 404.521 bytes y 831 filas, con `Closed Transactions`,
`Open Trades` y `Summary`, que pasa `validate_native_mt4_history_report` y del
que `live_account_mt4._summary` lee `('USD', 4830.53, 4829.67)`.
