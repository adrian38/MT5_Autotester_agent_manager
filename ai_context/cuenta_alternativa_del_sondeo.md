# La cuenta que se sondea en una tarjeta, cuando no es la suya

Petición del usuario (2026-11-17): «en las tarjetas de cuentas reales
agreguemos el icono de configuración que me permitirá en un modal guardar una
cuenta; adicionalmente dirá qué agente (broker) utilizar, seleccionándolo.
Entonces el sondeo de esta tarjeta se hará a esa cuenta configurada, al igual
que la pantalla de patrimonio; si no tiene ninguna sigue con la original. Esto
afecta sólo a estas dos pantallas».

- El almacén y la resolución: `mt5_manager/live_account/live_account_alternate.py`.
- El diálogo: `mt5_manager/static/live_account/live_account_alternate.js`, que
  sólo guarda la pareja «tarjeta → cuenta y agente».
- Lo que se guarda: `runtime/live_account_alternates.json`, con la contraseña
  cifrada con la **misma** clave que las del auditor
  (`LiveAuditSettingsStore.secret_cipher`). Una segunda clave sería otro
  fichero que copiar al mudarse y otra forma de perder una contraseña.
- Lo comprueba `tests/live_audit/test_live_account_alternate.py` y,
  del lado de la pantalla, `tests/manager/test_static_accounts_screen.py`.

## La terminal es parte de la lectura: MT4 se cierra

Ampliación del usuario (2026-11-17): el diálogo guarda también `mt4` o `mt5`.
El valor viaja en cada cuenta del payload de **los dos** sondeos —el diario y
la relectura completa del patrimonio— porque ambos llaman al mismo `launch`.

- MT5 conserva el contrato anterior: necesita `restore_*`, se anota cada
  terminal tocada y al terminar vuelve a la cuenta final configurada.
- MT4 usa el perfil activo `[MT4Terminal.N]` del agente lector, arranca con la
  cuenta indicada y exporta su `Account History`. Desde la corrección del
  2026-11-17, al terminar **cierra esa terminal**; no entra en
  `real_account_terminals` ni exige `restore_*`, porque no hay una cuenta final
  MT4 que restaurar.
- Una tanda mixta restaura sólo sus MT5. Una tanda compuesta enteramente por
  MT4 puede lanzarse aunque el agente no tenga cuenta final MT5 configurada.
- El informe MT4 tiene `Closed Transactions`, no la tabla de deals de MT5.
  `live_account_mt4_report.py` lo convierte a los mismos `RawDeal`, incluyendo
  ingresos/retiradas y el saldo reconstruido desde `Summary`, para que Cuentas
  en real y Patrimonio consuman exactamente la misma serie común.

El runtime que lo ejecuta está portado en AXI, que es el agente con
`MT4Terminal.1` configurado. Cambiar sólo el manager no habilita esa lectura.

## El perfil no se toca, y por eso esto no es un campo más del perfil

Cambiar `source_login` en el perfil del uso habría sido una línea. Lo que
cambia con ella es el emparejado de la auditoría, el historial y los lotes
declarados: la auditoría compara lo que el tester corrió contra lo que esa
cuenta ejecutó, y apuntarla a otra cuenta rompe la comparación entera.

El usuario lo dijo con todas las letras —«esto afecta sólo a estas dos
pantallas»— así que lo que se desvía es **la lectura**: quién entra en la
terminal y con qué credenciales. La tarjeta sigue siendo la del perfil, con su
login en el título y sus portafolios debajo.

## El agente forma parte de la configuración, y no es decorativo

Entrar en una cuenta de otro bróker necesita un terminal de ese bróker. Así
que el desvío mueve la lectura **de agente**: la tarjeta de AXI puede viajar
en el sondeo de RoboForex. Eso es lo que obliga a que la pregunta del módulo
sea «qué tiene que leer este agente» (`readings`) y no «qué cuentas tiene»
(`live_account_inventory.node_accounts`), que es lo que preguntaba antes
`snapshot_accounts`, retirado por eso mismo.

Tres consecuencias que no se ven en el diff:

- **El archivo sigue siendo el de la tarjeta.** Lo leído entra en
  `(nodo de la tarjeta, account_key)` y no en el del lector. Si se archivara
  bajo quien lo leyó, la portada y el patrimonio no verían nada y **nada lo
  diría**: la serie estaría guardada donde nadie la busca. De eso se encarga
  `Reading.owner_node_id`, que `live_account_ingest.collect` consulta antes de
  archivar.
- **La clave que viaja al nodo es la de la tarjeta**, no la de la cuenta
  alternativa: es con la que el agente devuelve su informe y con la que se
  archiva.
- **Un agente sin tarjetas propias puede tener trabajo.** `tick` saltaba un
  nodo sin cuentas; ahora también mira si alguien le desvió una.

## El patrimonio viene de balde, y por eso no hay nada suyo que tocar

La cita del patrimonio (`live_account_wealth_watch`) no habla con ninguna
cuenta: pide el mismo informe completo al mismo agente a través de
`live_account_watch.launch`. En cuanto el sondeo resuelve a quién leer, la
relectura resuelve igual. Lo único que cambió ahí es que `summing_accounts`
pasó a ser `summing_readings`: lo que decide si una cuenta **suma** sigue
siendo la tarjeta —y por eso la exclusión se mira por su nodo— pero lo que se
lee ya es la lectura.

### Cambiar de fuente invalida la serie anterior

Corrección del usuario (2026-11-17): mientras la alternativa MT4 fallaba al
arrancar, Patrimonio seguía sumando el último archivo de la cuenta original.
Eso hacía parecer que la configuración no había sustituido nada. El archivo
sigue bajo la clave de la tarjeta, pero su `login` y `server` prueban de qué
cuenta salió: si no coinciden con la fuente configurada, no se suma ni se
enseña como vigente.

La fila de Patrimonio se nombra con el login, servidor y terminal elegidos y
dice que la lectura completa está pendiente. Además, el sondeo diario pide
toda la vida cuando cambia esa identidad; extender sólo los últimos días
mezclaría la historia de la cuenta anterior con la nueva. El agente elegido
recibe **sólo** la alternativa: la cuenta original no viaja en ese payload.

## Sin desvíos, nada cambia de sitio

`readings` mira el estado de **todos** los agentes sólo si hay alguna tarjeta
desviada (`any_configured`). Con el almacén vacío, que es lo normal, recorre
únicamente su propio nodo y devuelve exactamente lo que devolvía
`snapshot_accounts`. Un cron que leyera el estado de los cuatro agentes en
cada vuelta para no encontrar nada habría sido pagar siempre por lo que casi
nunca se usa.

## Lo que se enseña, porque callarlo sería mentir

- **La tarjeta dice qué cuenta lee** (`.account-alternate`, en ámbar junto al
  servidor). El saldo, el beneficio y el drawdown de arriba son de esa cuenta
  mientras el título sigue siendo el login del perfil: sin la línea, los
  números se leen como del título.
- **El diálogo del sondeo también** (`live_account_schedule.js`): listaba el
  login del perfil, en el que ya no entra nadie.
- **Una tarjeta desviada deja de estar «SIN CREDENCIALES».** La cuenta que se
  sondea lleva su propia contraseña, y acusar de falta de credenciales a algo
  que se lee todas las noches manda a arreglar lo que no está roto. Es además
  el caso de uso: una cuenta cuyo perfil no tiene contraseña se arregla
  guardando otra.

## Dos cosas que se resolvieron callando a propósito

- **La contraseña sólo vuelve si se pulsa el ojo.** El campo vacío sigue
  significando «deja la que hay», pero el ojo común puede pedir al manager el
  secreto de esa tarjeta concreta. Al volver a ocultarlo se borra del campo y
  no se reenvía como si el usuario hubiera escrito un cambio.
- **Un token que ya no descifra no lee nada.** Clave cambiada o fichero traído
  de otro equipo: la tarjeta se queda sin sondear, que se ve en la portada,
  antes que volver en silencio a la cuenta del perfil, que no se vería.

## Lo que se movió de sitio por el camino

`live_account_ingest.py` estaba **exactamente** en el techo de 600 líneas y su
propio encabezado ya decía que recogía dos cosas distintas. La segunda —la
media estacional de cada portafolio, que no sale del sondeo sino de los
informes del backtest en la memoria UBS del agente— vive desde ahora en
`live_account_expectation_refresh.py`. Es un movimiento literal: lo que cambió
son los `patch()` de las pruebas, que apuntaban al módulo consumidor.

Y el periodo del sondeo (`live_account_watch._period`) pasó a medirse sobre
las lecturas en vez de sobre las tarjetas del nodo. Eso arregla de paso algo
que nadie había notado: una cuenta **bloqueada** no viaja en el sondeo, pero
su serie vacía arrastraba el periodo a «toda la vida» en cada vuelta.
