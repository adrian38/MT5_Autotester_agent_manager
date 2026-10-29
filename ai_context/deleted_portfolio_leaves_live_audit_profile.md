# Un portafolio borrado no conserva usos de auditoría activos

## El fallo (2026-10-29)

El borrado del inventario y la configuración de auditoría viven en persistencias
distintas. `PortfolioCoordinator` confirmaba el borrado en el nodo, pero el
`LiveAuditSettingsStore` conservaba sus perfiles. Si uno tenía
`live_trading=true`, la cartera eliminada seguía en «Portafolios activos» y
podía seguir entrando en el cron.

No se arregla ocultando la tarjeta: eso dejaría el uso activo para los procesos
de fondo. Después de que `_delete_on_node` confirma el borrado,
`remove_portfolio` elimina todos los usos que apunten a ese `portfolio_id`, los
saca de `selected_audit_ids` y borra sus credenciales cifradas. Los perfiles de
otras carteras permanecen intactos.

La limpieza ocurre sólo tras el éxito del nodo. Desmarcar una cartera en la
pantalla de auditorías sigue conservando su perfil para recuperarlo después;
esa acción no equivale a borrar el portafolio.

El cambio pertenece únicamente al manager. El nodo Vantage ya borraba la fila
y los ficheros manuales correctamente, por lo que no requiere port ni reinicio.
