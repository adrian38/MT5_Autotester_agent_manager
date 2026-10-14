# La portada no diagnostica una estrategia mientras queden reales sin atribuir

## Caso observado (2026-10-14)

La portada de portafolios activos mostraba el uso AXI del portafolio 105 con
`2 NO REPRODUCE` y tres avisos. Al abrir la auditoría, el déficit que alimentaba
esas etiquetas coexistía con operaciones reales sin referenciar. Esas
operaciones todavía podían pertenecer a las estrategias marcadas y cambiar su
PnL real al importarse la atribución.

La pestaña Análisis ya decía `Comparación parcial`, pero la portada eliminaba
esa salvedad y presentaba el cálculo parcial como diagnóstico definitivo.

## Contrato

Una estrategia marcada por déficit pasa a `ATRIBUCIÓN PENDIENTE` cuando queda
alguna operación real sin referenciar del mismo símbolo. No cuenta como
`NO REPRODUCE` ni como `AVISO` en los totales de la portada hasta que esas
operaciones se atribuyan o se excluyan.

La comprobación es por símbolo, no sólo por `missing_real`: una operación real
extra puede pertenecer a una estrategia aunque todas sus operaciones del tester
ya tengan pareja. Si varias estrategias comparten símbolo, todas conservan la
salvedad porque la portada no tiene evidencia para elegir una.

La curva Real global sigue incluyendo las operaciones pendientes conforme al
contrato de auditoría. Sólo cambia la etiqueta diagnóstica de la estrategia.
Las operaciones atribuidas suman a su estrategia y las excluidas dejan de
provocar la salvedad.

El cambio vive sólo en `dashboard_metrics.js` y `dashboard.js`, dentro del
manager. No cambia el análisis persistido ni requiere portar o reiniciar nodos.
