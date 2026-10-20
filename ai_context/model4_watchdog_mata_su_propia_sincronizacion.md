# El watchdog de Model=4 mataba la sincronización que él mismo forzaba

**Esto no vive en este repositorio.** El runner del tester es de los agentes
(`run_tests_watchdog.py`, `run_tests_runner.py`, `run_tests_reports.py`); aquí
no hay copia, y `rg` sobre el manager no encuentra nada. Se apunta igual porque
el síntoma llega a la campana del manager como «Auditoría fallida» y la
siguiente vez se va a diagnosticar desde aquí.

## El síntoma

El 20 de octubre de 2026 cayeron tres auditorías de AXI (#67, #105, #109) con:

```
Strategy Tester terminó con código 1: ERROR: MT5 Model=4 volvió a bloquearse
tras el reintento automático.
```

Todas se recuperaron solas en el reintento del cron, así que el daño fue ruido
en la campana y una hora larga de reloj, no datos perdidos.

## La causa: dos mecanismos que se contradicen

- `prepare_model4_history_preflight` **aparta a propósito la caché M1** del
  símbolo (`bases/<servidor>/history/<símbolo>/*.hcc`) para que MT5 tome el
  camino resistente de sincronizar M1 antes de pedir ticks reales. Sin eso, MT5
  arranca la descarga de ticks dentro de una ventana de ~400 ms en la que se
  reconecta, la cancela y escribe un reporte de 0 barras / 0 ticks.
- El detector de Model=4 medía progreso con **dos** señales: que el journal del
  tester se volcara a disco, y que crecieran los `.tkc` de
  `bases/<servidor>/ticks/`.
- La sincronización M1 **no hace ninguna de las dos**: escribe los `.hcc` que el
  preflight acababa de apartar. Así que el detector confundía con un atasco el
  trabajo que él mismo había pedido y mataba el terminal a los 40 s.

El log lo ordena solo, y la pista es cuánta caché hay que rehacer:

| Símbolo | Años en caché | Resultado |
| --- | --- | --- |
| `XAUUSD.sa` | 2014–2026 | muere en los dos intentos → auditoría fallida |
| `USDJPY.sa` | 2015–2026 | muere en el primero, pasa en el segundo |
| `BTCUSD.sa` | 2024–2026 | pasa a la primera |
| `ETHUSD.sa` | 2024–2026 | pasa a la primera |

En los dos intentos de `XAUUSD.sa` los `.tkc` se quedaron clavados en
`36 ficheros / 526,2 MB`: cero descarga de ticks, porque MT5 todavía estaba en
M1. El segundo intento ya dobla el plazo (`kick_after` × 2 = 60 s) y tampoco
llegó.

## El arreglo

El runner pasa al watchdog los `.hcc` que el preflight apartó
(`history_targets`) y rehacerlos cuenta como progreso. Un atasco de verdad sigue
disparando: si no se mueven ni los ticks, ni el journal, ni el histórico, la
firma no cambia, y por encima siguen el `stall_after=300s` y el
`max_runtime=1800s`.

Cuatro commits hermanos, porque el fichero era **idéntico byte a byte** en las
cuatro copias: AXI `3b029a5`, IC `e8a2acd`, FUTURE `49fc819`, RoboForex/dev
`a070020`. **Cada agente necesita reinicio** para cogerlo.

## Para la próxima

- `running_terminal64_count=10` en ese log: diez terminales MT5 vivos en la
  máquina. No es la causa —el detector mata por señal, no por lentitud— pero sí
  alarga cada sincronización y acerca cualquier plazo.
- La evidencia del fallo **no está en el estado del uso**: el reintento
  sobrescribe `log_lines`. Está en el disco del agente, una carpeta por
  ejecución: `runtime/<nodo>/live_audits/audit_<uso>/<marca>/runner.log`, con
  los journals del tester al lado como `watchdog_attempt_N`.
- `prepare_model4_history_preflight` rota **todos** los años del símbolo, no los
  del periodo auditado. Con el arreglo ya no mata, pero sigue siendo una
  resincronización de una década para auditar dos semanas. Si algún día estorba,
  ahí está el siguiente recorte; su docstring explica por qué rotar sólo el año
  del `ToDate` no bastaba.
