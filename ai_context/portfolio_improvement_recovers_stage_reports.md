# Un veredicto que cambia no puede borrar un original (2026-09-17)

## Síntoma

`Mejorar base` sobre el único portafolio de RoboForex (#123) aborta con:

```
No se pudieron reconstruir todas las estrategias originales;
la mejora no puede retirar ninguna sin evidencia
```

En los tres modos, siempre por la misma estrategia: XAUUSD H4
`XAUUSD_H4_GOLD_XAUUSD_H4_GOLD_5e988f6b_g002_s013_v008.set`, candidato 4348.

## Causa medida sobre la memoria real

`portfolio_allocations` de #123 guarda esa fila **sin** `oos_report_path`
(cadena vacía), mientras sus nueve compañeras sí lo tienen. Los cuatro informes
del candidato siguen en `G:\TRADING\MT5_Autotester_agent\reports\`:
`<stem>.htm`, `robust_004348_<stem>.htm`, `tick_004348_<stem>.htm` y
`tick6m_004348_<stem>.htm`.

Estado en la memoria, medido el 2026-09-17:

| Estrategia | candidato | robustez | final tick | final tick 6M |
| --- | --- | --- | --- | --- |
| XAUUSD 4348 | `rejected` | **sin fila** | sin fila | sin fila |
| Otras nueve | `accepted` | `accepted`/`rejected` | varias `rejected` | varias `rejected` |

Es decir: el agente rechazó el candidato **después** de guardar el portafolio y
borró su fila de robustez; con ella desapareció la única ruta que la asignación
guardaba. Las otras nueve también cambiaron de veredicto —`.US30CASH` y `DAL`
están hoy en `robustez=rejected`— pero conservan la ruta y por eso se
reconstruyen sin problema. El cambio de veredicto no es el fallo: el fallo es
que sólo esa fila perdió la ruta.

`metrics.inputs` de #123 lo dejaba escrito desde el principio:
`import_calculation_complete=False` e
`import_unmeasured_sets=['XAUUSD_..._v008.set']`. El portafolio se importó el
2026-09-15 a las 04:50, el mismo día que 1745a2c añadió a
`import_candidate_rows` la recuperación de `robust_<id:06d>_<stem>.htm`, pero
antes de ese commit. Hoy la importación **sí** recupera esa ruta (comprobado
llamando a `import_candidate_rows(['XAUUSD_..._v008.set'])`); el portafolio ya
guardado, no.

## Regla

`member_rows` acepta `project` y, sólo con él, recupera del disco los **dos
informes obligatorios** de un original cuya asignación los perdió:

| Etapa | Nombre en `reports/` | ¿Se recupera? |
| --- | --- | --- |
| Base 2020-2024 | `<stem>.htm` | Sí |
| Robustez 2025-2026 | `robust_<id:06d>_<stem>.htm` | Sí |
| Final Tick continuo | `tick_<id:06d>_<stem>.htm` | **No** |
| Final Tick 6M | `tick6m_<id:06d>_<stem>.htm` | **No** |

Los dos opcionales no se recuperan a propósito. Seis de los diez miembros de
#123 se guardaron sin `final_tick_report_path`; añadírselo hoy a uno solo
cambiaría su riesgo flotante y su aporte 6M respecto a los números con los que
se evaluó y guardó la cartera. Reconstruir una base no es recalcularla.

La convención de nombres vive en un único sitio, `mt5_manager/stage_reports.py`,
que usan tanto `import_candidate_rows` como `member_rows`. Antes estaba escrita
sólo dentro del bucle de la importación.

Cada fila recuperada queda marcada con `historical_reports_recovered`, y los dos
motores de mejora añaden un aviso nombrando los sets afectados: el usuario debe
ver que la base se reconstruyó con un informe cuyo veredicto ya no está vigente.

Esto es lo mismo que ya decía el invariante de la base original: `Mejorar base`
añade, nunca retira. Abortar porque una etapa cambió de veredicto **es** retirar
un original, sólo que sin decirlo.

## Alcance

- Los dos motores UBS normal, `portfolio_improvement_service.py` y
  `portfolio_improvement_chain_service.py`, pasan `project=source.project`. El
  cambio es idéntico en ambos porque `_load_full_history_improvement_pool` está
  en la lista `SHARED` de `tests/test_portfolio_improvement_chain.py`.
- `portfolio_monthly_improvement_service.py` **no** pasa `project`: sigue
  congelado y con el comportamiento anterior. La primitiva compartida gana la
  capacidad, no el uso. Si algún día se descongela el mensual, activarla ahí es
  añadir un argumento.
- Lo calcula el manager en `PortfolioCoordinator._worker`. La escritura sigue
  siendo del nodo y no cambia ninguna clave persistida: **no requiere port a
  `manager_node_runtime/`** de ningún agente.
- No se tocó la memoria de RoboForex. La fila de #123 sigue con
  `oos_report_path` vacío; lo que cambia es que la mejora ya no depende de ella.

## Verificación

Contra la memoria real de RoboForex (`PortfolioSource` sobre `G:\`), los tres
modos de #123 pasan de reconstruir 9 de 10 originales a reconstruir 10 de 10,
sin avisos de carga. La curva recuperada de XAUUSD da 395,25 en 2020-2024 y
397,07 en 2025-2026, y entra sin rendimiento 6M, igual que sus seis compañeras
sin `final_tick_report_path`.

Pruebas en `tests/test_portfolio_improvement.py`: la reproducción del #123, que
los opcionales no se inventan aunque estén en disco, y que una ruta guardada
manda sobre la recuperación. 71 pruebas focalizadas de mejora en verde y las 499
del descubrimiento completo salvo los 8 errores de importación previos por falta
del módulo `cryptography` en este equipo.
