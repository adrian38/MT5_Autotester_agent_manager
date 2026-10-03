# `ubs_agent.py` ya no declara sus opciones: lo que eso rompe

Síntoma: la pantalla de auditoría real (`live_audit.html`) no carga los
portafolios del nodo y enseña el aviso

```
No existe la memoria UBS:
C:\Users\Adrian\Adrian\TRADING\MT5_Autotester_agent_IC\MT5_Autotester_agent\outputs\ubs_memory.sqlite
```

La memoria que de verdad existe es `ubs_memory_ICTRADING_STANDARD.sqlite`. El
nodo pedía la **legacy**, que no existe desde que la memoria se separó por
broker y tipo de cuenta.

## Causa

El nodo no sabe qué opciones acepta el agente: **lee el texto de
`ubs_agent.py`** y busca literales `"--algo"`. Esa heurística tenía dos usos:

- `node_settings.filter_supported_options`: quitar del comando las opciones que
  una rama vieja de broker no expone.
- `node_settings.memory_path`: si el agente no conoce `--broker`, es una rama
  anterior al reparto por cuenta, así que su memoria es la legacy.

En ICTrading `ubs_agent.py` es ahora una **fachada de 487 líneas**: el parser
vive en `ubs_agent_cli.py` y el fichero no contiene **ni un solo** literal
`--opción`. La heurística leía cero opciones, concluía «no soporta `--broker`» y
devolvía la ruta legacy.

`filter_supported_options` ya se protegía de eso: si no encuentra
`--generations` da por hecho que es un envoltorio y no filtra nada.
`memory_path` no tenía esa salida.

El criterio vive ahora en un único sitio, `node_settings.declared_cli_options`,
que devuelve **`None` cuando el fichero no declara ninguna opción**: no es «no
soporta nada», es «no se puede saber». El centinela sigue siendo
`--generations`, que declara toda rama con parser propio. Cada consumidor
decide qué hace con el `None`:

| Consumidor | Con `None` |
| --- | --- |
| `memory_path` | memoria por broker y cuenta |
| `filter_supported_options` | no filtra ninguna opción |
| `universe_service.build_history_command` (sólo en el runtime) | no exige `--probe-universe-history` |

Una rama vieja de verdad sí declara `--generations` sin `--broker`, así que
sigue cayendo en la legacy; una fachada sin literales se trata como agente
moderno.

## Lo que hay que mirar al portar esto

- El fichero que ejecuta el agente es `manager_node_runtime/node_settings.py`,
  no `mt5_manager/node_settings.py`. Ver «El nodo NO ejecuta este repositorio».
- La copia del runtime **no importaba `re`**: el port se veía idéntico en el
  diff y reventaba con `NameError` en la primera llamada. `python -m
  tools.undefined_names <módulo>` lo caza; `import` del módulo, no.

## Segundo sitio, arreglado después

`manager_node_runtime/universe_service.build_history_command` exigía el literal
`"--probe-universe-history"` dentro de `ubs_agent.py` y, al no encontrarlo,
cortaba con «El agente no soporta --probe-universe-history»: el sondeo de
historial del universo quedó inalcanzable desde el nodo el mismo día que se
dividió el CLI. Ahora pasa por `declared_cli_options` como los otros dos.

Ese gate **no existe en el manager**: el universo lo resuelve el nodo, así que
`universe_service.py` sólo vive en `manager_node_runtime/`. Lo que sí está en
las dos copias es el helper.

Si aparece un cuarto sitio, el síntoma siempre es el mismo: una capacidad que
el agente sí tiene se da por ausente. Buscar `ubs_agent.py` en el runtime antes
de depurar el agente.
