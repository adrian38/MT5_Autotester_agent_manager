# Solo portafolio desde una carpeta de sets

## Contrato

La tarjeta del nodo ofrece `Solo portafolio`, una pantalla independiente de
Portafolio UBS, mensual y Grid. El navegador carga una carpeta mediante
`webkitdirectory`; sólo transporta los `.set` y limita el lote a 35 MB.

No se importan ni se inventan informes o métricas. Cada set debe declarar
`ForceSymbol`, `TimeFrame` y un `StartLots` positivo. La composición se guarda
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
