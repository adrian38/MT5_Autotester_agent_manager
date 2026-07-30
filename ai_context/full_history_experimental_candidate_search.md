# Búsqueda UBS experimental de candidatos

## Decisión

`experimental_full_search` es una ruta opt-in exclusiva de Portafolio UBS
normal. Está desactivada por defecto y no se comparte con
`experimental_monthly_search`.

La lógica vive en `mt5_manager/portfolio_full_experimental.py`. El orquestador
UBS normal la utiliza únicamente para seleccionar la composición base común
A/M/C. Las variantes agresiva, moderada y conservadora continúan calculándose
con `optimize_portfolio()` sobre esa misma composición bloqueada.

## Selección experimental

1. Conserva el contrato de entrada de las cuatro etapas aceptadas.
2. Mezcla rankings por score UBS, beneficio, R/DD, PF, estabilidad IS/OOS,
   recuperación Final Tick 6M y riesgo.
3. Ejecuta tres rotaciones deterministas por ronda. Cada estrategia aparece una
   vez en cada rotación con compañeros distintos.
4. Construye pools minimizando la máxima correlación positiva de Pearson,
   downside y solapamiento de drawdown.
5. Desactiva el recorte top-K dentro de cada pool y usa un presupuesto reducido
   en las rondas clasificatorias.
6. Hace avanzar aproximadamente la mitad según frecuencia de selección,
   contribución, archivo Pareto beneficio/DD y estabilidad por segmentos.
7. Ejecuta el presupuesto completo en el pool final.

## Auditoría

El resultado base incorpora
`seasonal_validation.experimental_full_history_stability` con:

- rendimiento agregado IS 2020-2024;
- rendimiento agregado OOS 2025-2026;
- recuperación y cobertura de Final Tick 6M;
- proporción anualizada OOS/IS;
- veredicto informativo `OK` o `REVISAR`.

La auditoría no veta automáticamente una cartera. El optimizador UBS estable
sigue siendo responsable del DD, margen, correlaciones finales, lotaje,
serialización y persistencia.

## Separación de scopes

La interfaz normal expone `experimental_full_search` solamente en
`portfolios.html/js`. La interfaz mensual conserva su checkbox y módulo
independientes. `normalize_settings()` fuerza a `false` el flag que no
corresponde al scope recibido.
