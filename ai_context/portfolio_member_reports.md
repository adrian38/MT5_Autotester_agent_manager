# Informes de una estrategia guardada

En el detalle de UBS normal, «Abrir reporte» no debe usar `os.startfile` ni
devolver una ruta local al navegador. El manager entrega el HTML como respuesta
`inline` y la interfaz abre inmediatamente una pestaña vacía desde el clic del
usuario para evitar el bloqueo de ventanas; después carga allí el `Blob`.

«Exportar reportes» descarga un ZIP con todos los HTML que existan para ese
miembro: base, robustez, Final Tick continuo, Final Tick 6M OHLC y Final Tick 6M
every tick. Las cuatro rutas persistidas en la asignación son autoritativas. La
memoria actual del candidato se consulta sólo para recuperar la ruta OHLC 6M,
que el esquema histórico de `portfolio_allocations` no guarda. Esa consulta es
opcional: un SQLite bloqueado o desmontado no puede impedir abrir o exportar los
reportes persistidos que todavía existen en disco.

La pertenencia se valida contra los miembros del portafolio antes de leer ningún
archivo. Todo ocurre en el manager y reutiliza datos de solo lectura; no requiere
cambios en `manager_node_runtime/`.
