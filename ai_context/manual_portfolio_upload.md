# Solo portafolio desde una carpeta de sets

## Contrato

La tarjeta del nodo ofrece `Solo portafolio`, una pantalla independiente de
Portafolio UBS, mensual y Grid. El navegador carga una carpeta mediante
`webkitdirectory`; sólo transporta los `.set` y limita el lote a 35 MB.

No se importan ni se inventan informes o métricas. Al seleccionar la carpeta,
el navegador lee UTF-8 o UTF-16 y muestra `ForceSymbol`, `TimeFrame` y
`StartLots` por estrategia. Símbolo y timeframe son editables; si falta el
timeframe intenta inferirlo del nombre (`ScalpH1_C.set` → `H1`). Antes de subir,
normaliza ambos campos dentro de una copia UTF-16 del set, sin cambiar el
original del usuario. Cada set debe conservar un `StartLots` positivo. La composición se guarda
con una unidad por fichero y el lote exacto de `StartLots`; beneficio y DD
quedan a cero con un aviso explícito de histórico pendiente.

## Quién persiste

El manager reenvía el lote a `POST /api/v1/portfolios/manual`. El runtime real
del nodo guarda las copias estables bajo
`runtime/<node>/manual_portfolios/<uuid>/sets/` y registra las asignaciones en
su memoria UBS local. Guardar los ficheros sólo en el manager no serviría: la
auditoría se ejecuta en el agente y tiene que poder resolver `set_path` allí.

Los nombres de fichero deben ser únicos aunque procedan de subcarpetas, porque
los reportes manuales y los artefactos de auditoría identifican cada estrategia
por el nombre del set.

## Histórico y auditorías

Después del guardado, la pantalla ofrece primero `Generar histórico OHLC`. Las
fechas inicial y final se leen de `FromDate` y `ToDate` del `tester_template.ini`
del nodo, se pueden editar y se validan como `YYYY.MM.DD`. La ejecución fuerza
`Model=1`, usa los sets persistidos de ese portafolio y entra en la misma cola
exclusiva que los demás trabajos del agente.

`Auditorías` abre `live_audit.html` con el id del portafolio. La pantalla
reconoce el parámetro `portfolio`, marca esa cartera y crea o recupera su uso de
auditoría. El modo elegido al crearla es una sola variante (`aggressive`,
`balanced` o `conservative`) y no se vuelve a elegir.

El botón no ejecuta MT5 ni inicia una auditoría: deja al usuario en la pantalla
de configuración, donde siguen siendo obligatorias las cuentas, credenciales y
el periodo.

## Inventario separado y progreso

Las filas manuales conservan `metrics.inputs.manual_portfolio=true`. El listado
general proyecta esa marca: `Portafolio UBS` excluye esas filas y
`Portafolios manuales` muestra sólo ellas. Auditorías no aplica el filtro porque
es el inventario común de ambos orígenes.

El histórico sí se ejecuta en el nodo como trabajo `manual_history`. La pantalla
de creación y el inventario manual sondean `/api/nodes` y el log del nodo para
mostrar cola, ejecución, finalización o fallo. Los hitos del log sólo aproximan
el porcentaje durante MT5; el estado `completed` es el único que marca 100 %.

El inventario no es una colección de enlaces: replica el patrón maestro-detalle
de UBS. Al seleccionar una cartera consulta su detalle, relaciona cada `.set`
con el HTML homónimo de `<proyecto>/reports` y muestra composición, periodo,
operaciones, beneficio, drawdown, acierto, profit factor, calidad y desglose
mensual. La lectura usa la caché por mtime/tamaño; no recorre el directorio de
reportes, que puede contener miles de artefactos.
