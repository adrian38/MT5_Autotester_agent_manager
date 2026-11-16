# El cambio de divisa lo refresca el sondeo (2026-11-16)

Seis de las catorce cuentas reales están en dólares y la moneda de los totales
es el euro, así que la portada de cuentas, el resumen del mes y el patrimonio
entero cuelgan de un número: `exchange_rates["USD"]`.

Hasta hoy ese número se escribía a mano en el diálogo del sondeo diario. El que
estaba sumando era `0,92`, guardado el **2026-11-10**; la referencia del BCE del
2026-11-16 daba `0,9079`. Un 1,3 % de diferencia sobre seis cuentas, sin que
nada en la pantalla distinguiera «el cambio de hoy» de «el que alguien tecleó
la semana pasada».

## Lo que hace ahora

`mt5_manager/live_account/live_account_rates.py`. Cuando un nodo **entrega** un
sondeo, `live_account_watch.tick` llama a `refresh_after_snapshot`, que baja
`eurofxref-daily.xml` del BCE y guarda los cambios con la fecha del feed.

Decisiones que el código no puede explicar solo:

- **La fuente es el BCE y no la cotización del bróker**, elegido por el usuario
  el 2026-11-16 entre las dos. La del bróker es el precio al que ese dinero se
  movería de verdad, pero el sondeo lo ejecuta el runtime bifurcado de cada
  agente: habrían sido cuatro ports hermanos —IC, AXI, RoboForex y Vantage— más
  el cambio de protocolo y cuatro reinicios, y además cada bróker daría un
  precio distinto. El feed del BCE no lleva clave, no mete a un tercero y cae
  entero en este repositorio. El manager ya sale a Internet para los avisos
  push, así que la capacidad existía.
- **Un fallo conserva el cambio anterior y avisa** (`kind:
  exchange_rates_stale`), también elección del usuario. Borrarlo habría
  convertido un corte de red en un patrimonio incompleto: seis cuentas de
  catorce fuera del total.
- **Se mira una vez por día UTC**, y la marca se pone **antes** de salir a la
  red. El `tick` pasa cada pocos segundos: sin eso, un feed caído repetiría la
  descarga y el aviso en cada vuelta y enterraría la campana.
- **Decide la fecha del feed, no comparar los números.** El cambio calculado es
  `0,919963…` y el guardado el `0,92` que alguien tecleó: comparar `float`
  habría dicho «ha cambiado» todos los días y reescrito el fichero para nada.
  La excepción es una divisa que falte, que con la misma fecha sí se rellena.
- **Se funde, no se sustituye.** Una divisa que el BCE no publica conserva lo
  escrito a mano en vez de desaparecer.
- **El espacio de nombres del XML no se declara.** Se recorre por el final del
  nombre de la etiqueta. El BCE lo ha cambiado antes, y declararlo y que cambie
  no da error: devuelve cero divisas y se guarda un diccionario vacío.

## Dos trampas que ya mordieron

- **El feed del BCE trae su propia fecha, y no es la de hoy.** Publica en días
  hábiles hacia las 16:00 CET, así que un domingo lo que devuelve es del
  viernes. `rates_updated_at` guarda esa fecha —no la de la descarga— y el
  diálogo la enseña; aparentar que el dato es de hoy era justo el problema que
  esto venía a arreglar.
- **Un doble de prueba incompleto deja la guarda en verde.** `FakeServer` no
  tenía `update_live_account_scheduler`, así que `tick` daba el refresco por
  fallido, lo escribía en `stderr` y las veintiséis pruebas del cron pasaban
  **descargando el feed del BCE de verdad**. Al arreglarlo, el fichero se pasó
  del techo de 600 líneas y sus dobles salieron a
  `tests/live_audit/live_account_fixtures.py`. Cuando el `tick` aprenda a
  llamar a algo nuevo, el sitio donde se nota es ese doble.

## Lo que sigue compartido entre las dos pantallas

El `display_currency` y los `exchange_rates` viven en la configuración del
sondeo diario, y el patrimonio los usa para sumar. Eso es **dato, no cita**: un
cambio EUR→USD no es de una pantalla ni de la otra, y duplicarlo daría dos
cifras que discrepan. El usuario lo confirmó el 2026-11-16, el mismo día en que
mandó separar las dos citas —ver la sección «El sondeo diario no se abre desde
el patrimonio» de `live_audit_cron_and_bell.md`.
