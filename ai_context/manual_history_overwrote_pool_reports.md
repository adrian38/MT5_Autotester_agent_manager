# El histórico OHLC manual pisaba el informe base de un candidato (2026-11-08)

## Síntoma

`Mejorar base` del portafolio #67 de AXI, modo Agresivo, abortaba con:

```
No se pudieron reconstruir todas las estrategias originales;
la mejora no puede retirar ninguna sin evidencia
```

No era el filtro `active_portfolios_only` recién añadido (b86a810): ése se
aplica al pool de candidatas, y la reconstrucción de los originales ocurre
antes, en `_load_original_improvement_sets`.

## Causa medida sobre los datos reales

Los cuatro originales de #67 reconstruían **0 de 4**. El aviso que el motor
descartaba decía la causa:

```
ValueError: First report period must end before second report period starts
```

Los cuatro informes estaban ahí y se leían; lo que no cuadraba eran sus
períodos:

| Set | Base (IS) | Robustez (OOS) | mtime base |
| --- | --- | --- | --- |
| XAUUSD 3813 | 02.01.2020 → **19.05.2026** | 02.01.2025 → 29.05.2026 | 08-11 01:13 |
| USDJPY 20578 | 02.01.2020 → **21.05.2026** | 02.01.2025 → 26.05.2026 | 08-11 01:13 |
| BTCUSD 21175 | 14.01.2020 → **18.05.2026** | 02.01.2025 → 28.05.2026 | 08-11 01:13 |
| ETHUSD 46978 | 02.01.2020 → **20.05.2026** | 07.01.2025 → 28.05.2026 | 08-11 01:12 |

Un candidato intacto del mismo run (3394) conserva la ventana buena:
`03.01.2020 → 30.12.2024`.

El portafolio manual #112 «AXI_Select_2» se creó ese día a las 01:11 con cuatro
`.set` que son los mismos cuatro de #67 **en minúsculas**. A las 01:12-01:14 se
le pidió `Generar histórico OHLC`, que lanza `run_tests.py` con las fechas de
`tester_template.ini` (`2020.01.01` → `2026.05.22`). De los 859.897 ficheros de
`reports/`, los 36 tocados ese día son exactamente esos cuatro sets y sus
`.png`/`.mt5log.txt`.

## Por qué la colisión es estructural

- Todo backtest del agente deja su informe en la **única** carpeta `reports/`.
- `run_tests_experts._report_artifact_paths` lo nombra `safe_name(set.stem)`,
  sin distinguir de qué trabajo viene.
- `portfolio_manual_reports.report_for_set` empareja el histórico manual con su
  set **por ese mismo nombre**: es la regla única y no se puede cambiar sin
  romper el resumen manual y la tabla «contra el histórico» del auditor.
- NTFS es insensible a mayúsculas, así que `btcusd_….htm` **es**
  `BTCUSD_….htm`. Ni siquiera hace falta subir el nombre exacto.

No hay copia de seguridad: no quedaba ninguna otra copia de los cuatro `.htm`
en el checkout.

## Lo que costó recuperarlos

Se relanzó el backtest IS `2020.01.01 → 2024.12.31` sobre los `.set` del
**candidato** —no los del portafolio manual, que `normalize_manual_set` había
reescrito— con `Model=1`, 1.000 EUR y 1:500, el único perfil habilitado de AXI
(`MT5_AXI_8`). La ventana se confirmó contra un candidato intacto del mismo
run.

**No sale igual.** El informe original es de la build 5836 y hoy el terminal es
la 6230, con otro histórico de minutos: ETHUSD reprodujo al céntimo, BTCUSD
−0,5 %, XAUUSD −6,4 % y USDJPY −12,7 %, con un 3 % menos de operaciones en los
dos que se movieron. Sobre la curva 2020-2026 que usa el portafolio son −4,1 %
y −9,5 %, porque la mitad 2025-2026 sale del informe de robustez, que es otro
fichero y quedó intacto: coincide al céntimo en los cuatro.

Los cuatro siguen pasando los umbrales con margen, así que el repuntuado desde
su informe no movió ningún veredicto. Se hizo sólo sobre esos dos candidatos
con `ubs_agent_rescore._rescore_candidate_report`, la pieza por fila del
agente: el modo completo recorre los 76.000 y, sin `--rescore-from-reports`,
llama a `cleanup_stale_stage_rows()`, que es el mecanismo que dejó a
RoboForex #123 sin la ruta de su etapa de robustez.

## Prohibir el nombre era el arreglo equivocado

El primer intento rechazaba el `.set` al subirlo si su nombre ya era el de un
informe del pool. **Eso prohibía el caso normal.** Un portafolio manual es un
uso en real: `live_portfolio_ids` de AXI son `[67, 105, 108, 109, 110, 112]` y
tres de los seis son manuales. Subir las estrategias que la cuenta opera de
verdad —que suelen ser candidatos aceptados— es exactamente para lo que existe
la pantalla.

## La regla

El histórico corre sobre una **copia renombrada**, igual que la comprobación
manual usa `check_<n:03d>_<set>`:

| Dónde | Qué hace |
| --- | --- |
| `manual_portfolio_history.manual_report_stem` | `manual_<portafolio:06d>_<set>`: el nombre del informe. Lleva el portafolio porque dos carteras manuales pueden traer el mismo `.set` con fechas distintas. |
| `manual_portfolio_history._history_sets` | Copia cada `.set` ya normalizado a `<uuid>/history_sets/` con ese nombre. Se rehace en cada ejecución, así que un miembro retirado deja de correr. |
| `portfolio_manual_reports.report_for_set` | Busca `manual_<portafolio>_<set>` y luego el nombre pelado, que es como quedaron los históricos anteriores. Sin `glob`: `reports/` pasa de 850.000 ficheros. |
| `manual_portfolio_history._refuse_pool_report_overwrite` | Última red, sobre las **copias**: lo que importa no es cómo se llama lo que subió el usuario, sino qué nombre se va a escribir. |

«Del pool» se decide en la memoria UBS, no en el disco: `pool_report_names`
reúne los `report_path` de `candidates`, `candidate_robustness`,
`candidate_final_tick` y `candidate_final_tick_6m`. Los dos lados se comparan
tras pasar por `report_stem`, que aplica el mismo `safe_name` que el runner.

El número del portafolio tiene que llegar hasta `report_for_set`, así que
`manual_history_summary` y `live_audit_degradation.read_histories` lo reciben;
el auditor lo saca de `result["portfolio_id"]`.

## Alcance

- Es código **del nodo**: `manager_node_runtime/manual_portfolio.py` y
  `manual_portfolio_history.py`. El manager sólo reenvía el lote, así que no
  tiene copia que cambiar. Portado a IC, AXI, RoboForex y Vantage; FUTURE
  omitido por congelación. **Los cuatro agentes hay que reiniciarlos.**
- Lo vigila `tests/node/test_node_runtime_manual_portfolio_parity.py`, que
  además exige las cuatro etapas y nombra las copias que no pudo verificar.
- No cambia ninguna clave persistida ni el protocolo HTTP.

## El mensaje que no decía nada

El motor **tenía** la causa medida y la tiraba: `load_robust_sets_from_rows`
devuelve `(sets, warnings)` y los avisos nombran el set y el error, pero el
`raise` sólo repetía la frase genérica. Averiguar lo de arriba costó abrir la
memoria del agente y medir los informes a mano.

`unreconstructed_originals` (en `portfolio_improvement_common.py`) construye
ahora el error con los originales que faltan y los avisos del cargador. Lo usan
los dos motores UBS normales —es una de las funciones que
`tests/portfolio/test_portfolio_improvement_chain.py` exige idénticas— y
también el mensual, con su propia consecuencia. Sobre #67 el mensaje pasa de la
frase sola a nombrar los cuatro sets y el solape de períodos.
