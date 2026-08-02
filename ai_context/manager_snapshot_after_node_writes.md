# Toda escritura hecha por el nodo obliga a invalidar la copia del manager

## Cómo lee el manager la memoria del nodo

Cuando el nodo tiene `portfolio_project_dir`, el manager **no** proxifica las
lecturas: abre la memoria SQLite él mismo (`manager.py`, ruta
`GET /api/nodes/<id>/portfolios`). Si el sistema de ficheros no soporta el `-shm`
del modo WAL —bind mounts 9p/virtiofs de Docker, CIFS— `connect_memory` no lee el
original sino una **copia** que hace `_remote_read_snapshot`.

Esa copia se reutiliza cuando se cumple cualquiera de estas dos cosas:

1. la firma `{tamaño, mtime_ns}` del original y de su `-wal` no ha cambiado, o
2. la copia se hizo hace menos de 30 s.

## Por qué eso rompe lo que acaba de escribir el nodo

Las escrituras las hace el nodo, no el manager, así que el manager no se entera.
Y sobre un bind mount el tamaño y la mtime que ve el contenedor van por detrás
del contenido real (Windows no actualiza la entrada de directorio de un fichero
que otro proceso mantiene abierto). Con la firma aparentemente intacta, la
condición 1 se cumple y el manager sigue sirviendo la copia anterior. La
condición 2 añade una ventana de 30 s con el mismo efecto.

La interfaz repinta la lista **inmediatamente** después de guardar
(`portfolios.js`: `Promise.all([loadManagerState(), loadPortfolios(id)])`) y no
la vuelve a pedir por su cuenta: sólo el botón «Actualizar» o una exclusión o un
borrado la refrescan. Por eso el fallo se ve como «dice que se guardó pero no
aparece» y no se corrige solo.

Ocurrió con el portafolio A/M/C #11 de AXI (02.08.2026 03:42): la fila, sus 18
asignaciones y su registro de decisiones estaban completos en
`ubs_memory_AXI_STANDARD.sqlite`, pero la lista del manager no lo mostraba.

## Regla

**Después de cada escritura confirmada por el nodo hay que borrar la firma de la
copia.** `_invalidate_remote_snapshot` hace justo eso: sin metadatos, `copied_at`
vale 0 y la firma no casa, así que ninguna de las dos condiciones se cumple y la
siguiente lectura vuelve a copiar.

| Operación | Dónde se invalida |
| --- | --- |
| Borrado | `PortfolioCoordinator._delete_on_node` |
| Exclusión | `PortfolioCoordinator.exclude` e `invalidate_after_exclusion` |
| Guardado | `PortfolioCoordinator.confirm_save` → `_invalidate_node_snapshots` |

`_invalidate_node_snapshots` no propaga errores: se llama cuando el nodo ya ha
confirmado la escritura, y fallar ahí convertiría una operación correcta en un
error de interfaz. Si el nodo no tiene `portfolio_project_dir` sale sin hacer
nada, porque entonces no hay copia: las lecturas se proxifican al nodo.

Vale igual para los dos ámbitos, UBS completo y UBS mensual: comparten la misma
memoria y la misma copia.

## Al añadir una operación de escritura nueva

Si se escribe desde el nodo y el manager lee esa memoria, invalidar la copia en
el manager forma parte de la operación, no es un detalle opcional. Un test que
sólo compruebe la fila en SQLite pasa aunque la pantalla no la enseñe: hay que
comprobar además que la firma de la copia desaparece (ver
`test_confirming_a_save_forces_the_next_read_to_recopy_the_node_memory`).
