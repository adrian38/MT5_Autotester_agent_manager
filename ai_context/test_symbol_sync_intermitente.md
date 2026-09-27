# `test_symbol_sync` falla de vez en cuando y no es del código

`tests/test_symbol_sync.py::test_manager_reaches_ic_fork_syncs_and_launches_existing_probe`
falla de forma intermitente al correr el suite entero, y **pasa siempre en
aislamiento** (comprobado varias veces seguidas el 2026-09-25).

## Qué se sabe

- La prueba levanta un servidor HTTP y habla con el fork de ICTrading. En las
  ejecuciones que fallan aparece al final, ya fuera del informe de unittest,
  una línea suelta:
  `[manager-http] "POST /api/nodes/broker-test/universe-sync HTTP/1.1" 502 -`.
  Es una respuesta que llega tarde, de un hilo del servidor que sigue vivo
  cuando la prueba ya terminó.
- No depende de los cambios que se estén haciendo: se ha visto con el árbol en
  tres estados distintos, incluido uno que no tocaba nada de símbolos.

## Qué hacer cuando aparezca

Volver a correrla sola. Si pasa, no es una regresión: anotarlo y seguir. Lo que
**no** hay que hacer es depurar el código de sincronización de símbolos por
esto, ni aumentar un tiempo de espera a ciegas.

Si alguna vez falla también en aislamiento, entonces sí es real y hay que
mirarla. Arreglar la intermitencia de verdad pasa por que la prueba no deje
hilos del servidor atendiendo después del `tearDown`.
