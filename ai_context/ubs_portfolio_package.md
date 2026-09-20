# `ubs_portfolio`: de 6.400 líneas a trece módulos en pila

## Qué se ganó, con números

| | Antes | Ahora |
| --- | --- | --- |
| Ficheros | 1 | 13 + `__init__.py` |
| Leerlo entero | ~65k tokens | ~72k tokens |
| Cambiar el modelo de margen | ~65k | **~11k** |
| Tocar la selección de candidatos | ~65k | **~9k** |
| Ajustar la búsqueda greedy | ~65k | **~15k** |

El total **sube** ~7k: cada módulo repite su cabecera de imports y el
`__init__.py` cuesta ~3,3k tokens por sí solo, y se lee casi siempre. La ganancia
no está en el total, está en que ahora leer una parte es correcto: antes no había
forma de saber si la lógica que buscabas estaba en la línea 900 o en la 5.800.

## El orden es el de dependencia

`symbols` → `models` → `rows` → `curves` → `reports` → `selection` →
`evaluation` → `margin` → `constraints` → `execution` → `greedy` → `optimize` →
`strict_monthly`

El reparto no se eligió por temas sino midiendo: se construyó el grafo de
referencias entre las 201 definiciones de nivel superior y se buscó una
estratificación sin aristas hacia arriba. El primer intento tenía cuatro ciclos,
todos por la misma causa: **la cola del fichero no era una capa alta, era el
fondo**. `portfolio_symbol_key`, `portfolio_group_key`, `_row_value`,
`group_limits_for_portfolio_type` y `_lot_size_step` vivían al final por accidente
histórico y de ellas depende todo. Hoy están en `symbols`, `models` y `rows`.

Lo hace cumplir `tests/test_ubs_package_layering.py`.

## Lo que rompe un troceo así (y aquí rompió)

1. **`unittest.mock.patch` sobre el paquete deja de alcanzar al consumidor.**
   `test_loader_rejects_short_final_tick_as_continuous_history` parcheaba
   `portfolio_manager.ubs_portfolio.period_report_from_strategy_report`, pero
   quien lo usa es `load_robust_sets_from_rows`, en `selection`, con su propia
   referencia al nombre. Hay que parchear el módulo consumidor.
   Buscarlos con un grep multilínea: la cadena suele ir en la línea siguiente al
   `patch(`, así que `rg 'patch\("portfolio_manager'` **no** los encuentra.
2. **Tests que leen el fichero como texto.** `test_portfolio_margin_profiles`
   comprobaba una cadena en `ubs_portfolio.py`. Se cambió a leer el paquete
   entero, para que no dependa de en qué módulo viva el aviso hoy.
3. **Los imports relativos suben un nivel.** `from .mt5_report import` pasa a
   `from ..mt5_report import`.
4. **El baseline de `function_length` lleva la ruta en la clave.** Se migró
   remapeando por `qualname` y comprobando que ninguna función había crecido;
   regenerarlo habría aceptado cualquier crecimiento en silencio.

## Cómo se comprobó que no cambió nada

- Comparación definición a definición del fichero previo contra el paquete: 201
  definiciones, texto idéntico byte a byte. El troceo movió bloques y calculó
  imports; no tocó una línea de lógica.
- Banco diferencial contra `HEAD` (ver `function_length_ratchet.md`): 250
  escenarios sobre `optimize_portfolio`, cero divergencias.
- Suite completa, 623 tests.

## Flakiness preexistente

`test_integration.LocalIntegrationTests.test_application_restart_reaches_the_embedded_node`
falla de forma intermitente (~1 de cada 5 pasadas completas, nunca aislado).
Es de temporización del arranque HTTP y no tiene relación con este paquete.
