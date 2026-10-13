# Reportes tester manuales en una auditoría guardada

La pantalla de resultado permite sustituir uno o varios reportes del Strategy
Tester sin volver a ejecutar MT5. El manager reenvía los HTML al endpoint del
nodo `POST /api/v1/live-audits/<audit_key>/reports`; el nodo es quien valida,
guarda el artefacto y actualiza `last_payload`. El manager vuelve a aplicar su
criterio sobre esa materia prima y devuelve el resultado recalculado.

La identidad no se adivina por símbolos, magic ni prefijos libres. El archivo
debe llamarse exactamente como `strategy_artifacts[].source_set`, sustituyendo
`.set` por `.htm` o `.html`. El nodo además exige HTML interpretable de MT5,
History Quality 100 %, el mismo símbolo y el mismo timeframe. Conserva SHA-256,
fecha, nombre recibido y `report_source=manual_upload`; el reporte automático
anterior no se sobrescribe.

La sustitución cambia juntas las operaciones tester, su contador, calidad y
artefacto de la estrategia. Las demás estrategias permanecen intactas. La
carga se rechaza si la auditoría está en curso, no conserva `last_payload`, el
nombre no es único o cualquier reporte del lote falla la validación.

El caso AXI `audit-105-mv4perus-1` contiene realmente tres reportes al 40 %:
los dos XAUUSD H1 aportados manualmente y otro XAUUSD D1. Aplicar los dos HTML
H1 deja esos dos al 100 %, pero el D1 continúa marcado al 40 % hasta que exista
su propio reporte válido.
