# Limpieza histórica desde las tarjetas

- El botón del agente ejecuta, en este orden, `scripts/cleanOldTest.ps1` y
  `scripts/cleanOlddata.ps1`.
- La operación cierra MetaTrader, MetaTester y MetaEditor y elimina datos de
  `tester`, `bases`, `history` y reportes de las carpetas de datos de todas las
  terminales del usuario. No elimina los reportes locales del proyecto usados
  por UBS.
- El nodo expone la capacidad `historical_cleanup` solamente cuando encuentra
  ambos scripts. El manager muestra el botón manual y la opción automática
  únicamente en ese caso.
- La limpieza manual usa `POST /api/nodes/<id>/cleanup`, que el manager reenvía
  a `POST /api/v1/jobs/cleanup`. Es una tarea encolable y requiere confirmación
  destructiva en el navegador.
- Una generación añade `cleanup_tester`, `cleanup_data` y `cleanup_verify` al
  final de cada ciclo cuando `cleanup_after_run` está activo. Su valor
  predeterminado es activo siempre que ambos scripts estén disponibles.
- Si un script de limpieza falla, el nodo intenta igualmente los pasos de
  limpieza restantes, marca el job como fallido y no comienza el ciclo siguiente.
- `cleanup_verify` comprueba que no queden archivos en los árboles históricos
  de `%APPDATA%\MetaQuotes`.
- Los botones manuales `Reparar` y `Prueba regresiva` envían siempre
  `cleanup_after_run: true`. El nodo intercala las tres etapas de limpieza
  después de cada run seleccionado, antes de comenzar el siguiente run del lote.
