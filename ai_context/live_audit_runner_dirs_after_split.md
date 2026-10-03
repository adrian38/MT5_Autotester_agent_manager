# La auditoría real perdió los reportes al partirse `run_tests.py`

Síntoma: la auditoría de cuenta real termina el Strategy Tester sin un solo
error, y aun así falla en cuanto va a leer los reportes:

```
La auditoría falló; restaurando las terminales: MT5 no generó el reporte de
XAUUSD_H1_Other_sets_XAUUSD_H1_Other_f2d4d441_g002_s025_v005.set
```

Visto el 2026-10-03 en ICTrading (`audit-53`, 16 sets, cinco terminales). El
nombre que aparece es simplemente el **primer** miembro del portafolio: no
faltaba ese reporte, faltaban los dieciséis.

MT5 sí los había generado. Estaban en `<proyecto>\reports\`, con sus `.htm`,
sus `.png` y sus `.mt5log.txt`, mientras que el `reports/` del área de trabajo
de la auditoría sólo tenía el reporte de la cuenta real. El `configs/` de la
auditoría estaba vacío y los dieciséis `.ini` —**con la contraseña del
tester**— se habían quedado en `<proyecto>\configs\`.

## Causa

`_run_tester` lanza el runner con un `python -c` que reapunta sus carpetas al
área de trabajo de la auditoría. Hasta el 2026-09-29 eso bastaba, porque
`run_tests.py` **definía** `REPORT_DIR`, `CONFIG_DIR` y `LOG_DIR`.

El commit `afbde9f` («runner: dividir run_tests.py en nueve modulos por area»)
las bajó a `run_tests_base`, y cada consumidor se quedó con su propia
referencia:

| Quién | Para qué | ¿Lo alcanzaba el lanzador? |
| --- | --- | --- |
| `run_tests.REPORT_DIR` / `CONFIG_DIR` | sólo el `mkdir` inicial | Sí, y por eso las carpetas vacías existían |
| `run_tests.LOG_DIR` | el log de la tanda | Sí: `logs/` era la única que funcionaba |
| `run_tests_experts.REPORT_DIR` / `CONFIG_DIR` | la ruta del reporte y del `.ini` que recibe MT5 | **No** |
| `run_tests_reports.REPORT_DIR` | destino de la copia y glob de búsqueda y borrado | **No** |

Es la trampa del `patch()` que se queda sin efecto al mover código, descrita en
`AGENTS.md`, pero en código de producción: el lanzador seguía parcheando la
fachada y el suite seguía en verde.

Dos consecuencias, no una:

- la auditoría no encuentra ningún reporte y aborta en `_collect_tester_reports`;
- `_discard_tester_secrets` barre el `configs/` de la auditoría, que está
  vacío, así que los `.ini` con la contraseña del tester **sobreviven** en el
  proyecto del agente.

## Arreglo

`_tester_wrapper` ya no reapunta sólo `run_tests`: recorre `sys.modules` y
reapunta cada módulo `run_tests*` que declare alguno de los tres nombres.
Reapuntar también `run_tests_base` cubre a quien se importe más tarde.

Está en las dos copias —`mt5_manager/live_audit_tester.py` y
`manager_node_runtime/live_audit_tester.py` de IC, que es la que ejecuta— y lo
comprueban `tests/test_live_audit_tester_wrapper.py` y
`tests/test_manager_node_live_audit_wrapper.py`: montan un runner de mentira
partido igual que el de verdad, ejecutan el lanzador y exigen que los ocho
nombres apunten al área de trabajo. Con el lanzador antiguo, cinco de los ocho
se quedan en las carpetas del proyecto.

## Lo que queda por hacer a mano

Los `.ini` con la contraseña y los reportes de las auditorías ya corridas
siguen en `<proyecto>\configs\` y `<proyecto>\reports\` de cada agente. No los
borra nada: hay que barrerlos (`audit_*.ini`, `audit_*`) en cada copia.
