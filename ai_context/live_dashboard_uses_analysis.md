# La portada reutiliza el veredicto de Análisis

## Error observado (2026-10-14)

La portada y la tabla `Demo contra real` calculaban reproducción en dos
implementaciones distintas. La tabla permite simular lotes y guarda esa vista
en `localStorage`; la portada ignoraba esa configuración y podía mostrar
`NO REPRODUCE` donde Análisis mostraba `REPRODUCE`.

Dos intentos de corregirlo añadieron reglas exclusivas de la portada
(`ATRIBUCIÓN PENDIENTE` y normalización automática al lote esperado). Ambos
eran conceptualmente incorrectos: `NO REPRODUCE` y `AVISO` siguen siendo
veredictos válidos con comparación parcial, y la simulación del usuario no debe
convertirse silenciosamente en el criterio permanente de la auditoría.

## Contrato

`live_audit_reproduction.js` es la única primitiva para:

- aplicar la simulación manual o `Escalar demo al lote real`;
- sumar Demo y Real por estrategia, incluidas las reales atribuidas;
- calcular el déficit y resolver `REPRODUCE`, `AVISO`, `NO REPRODUCE` o
  `MEJOR QUE EL TESTER`.

Análisis y la portada cargan esa misma primitiva. La portada lee exactamente
`live-audit-lots:<nodo>:<uso>` del mismo navegador, por lo que refleja la vista
que el usuario dejó aplicada en Análisis. Sin simulación guardada muestra el
veredicto bruto, igual que la tabla al pulsar `Restablecer`.

Las salvedades (`POCAS OPERACIONES`, comparación parcial y calidad tick) siguen
viviendo en Análisis; no sustituyen el veredicto.

## Reinicio desde la portada

El botón inicial enviaba el `POST` pero no sondeaba el estado. Quedaba bloqueado
y no recargaba la página, aunque el trabajador hubiese reconstruido el manager.
Ahora sigue `GET /api/manager/restart`, tolera el corte de conexión durante el
reemplazo, recarga al completar y abre Generación si GitHub necesita
autorización, donde ya existe el diálogo con el código de dispositivo.

Todo esto vive en el manager; no requiere port a los runtimes de los agentes.
