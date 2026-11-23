# Experimenta Prop: tres fuentes para aprobar evaluaciones

Pantalla aislada `/prop_experiment.html`. Su objetivo no es maximizar retorno
ni crear un portafolio UBS: busca una composición cuya simulación histórica
apruebe una evaluación prop con sus límites de pérdida y objetivo.

## Pool y brokers

- Lee ICTrading, AXI y RoboForex mediante el mismo `readonly_source` de
  Experimenta. Vantage se muestra, pero queda excluido por el valor estable
  `portfolio_broker == VANTAGE` tanto en la interfaz como en el backend.
- Hay tres fuentes independientes: candidatas actuales, seeds UBS originales y
  estrategias de portafolios manuales. Las candidatas conservan las cuatro
  etapas aceptadas de `candidate_rows`.
- La fuente seed exige además `seed_final_tick_6m.status='accepted'` y
  `accepted=1`, con la misma huella `seed_mtime`/`seed_size` que el sondeo
  `seed_final_tick`. No significa «toda seed aceptada».
- La fuente manual recorre todos los portafolios marcados como manuales, no
  sólo los activos. Si el miembro sólo tiene el histórico OHLC manual completo,
  reutiliza `report_for_set` y `single_pass_report_path` para partir la curva.
- «Solo estrategias presentes en portafolios activos» no es una fuente: es un
  filtro transversal sobre la unión seleccionada. Resuelve los usos
  `live_trading` al iniciar cada cálculo y respeta la variante exacta del bundle.
- Los duplicados se resuelven por identidad lógica del nombre del set, con
  prioridad candidata → seed → manual; una fuente nunca sustituye la candidata.
- La cuarentena es un filtro transversal igual que «sólo activos»: se cruza por
  nombre exacto y por identidad lógica antes de deduplicar. Así una seed o una
  estrategia de portafolio manual no puede reintroducir un set excluido por
  degradación, OHLC, fallo en real o descarte manual. El origen informa cuántas
  filas retiró la cuarentena.
- Los símbolos deshabilitados se leen de los ajustes `full_history` de cada
  broker y se excluyen de sus tres fuentes antes de buscar la composición. La
  pantalla los muestra por broker y abre la misma gestión por símbolo de UBS:
  desde ahí se habilita o deshabilita el símbolo y se cambia el estado de cada
  set usando las rutas existentes de exclusión y reclasificación. La decisión
  nunca se comparte entre brokers.

`PortfolioSource.saved_portfolios()` devuelve un estado con la lista dentro de
`portfolios`, no la lista directamente. El primer recorrido de portafolios
manuales iteró las claves de ese estado y falló al terminar las seeds con
`'str' object has no attribute 'get'`. El cargador manual consume desde entonces
la clave `portfolios`, y el doble de prueba reproduce la forma real del contrato.

## Simulación

Cada estrategia entra con su posición mínima ejecutable real. Los trials empiezan
en distintos días históricos, siempre que quepa la duración completa de todas
las fases. Se comprueban objetivo, pérdida diaria, pérdida máxima estática o
trailing EOD, días mínimos, días rentables, consistencia y margen disponible.

Desde el 2026-11-22 la pantalla no expone topes de estrategias, unidades, pool
ni trials. Esos campos recortaban la búsqueda que se pretendía optimizar. El
motor carga todas las candidatas y seeds válidas, prueba todos los inicios que
caben en su ventana de simulación y añade posiciones mínimas ejecutables mientras
mejore el resultado y quede margen de la cuenta prop. El lotaje y la composición
son resultado, no configuración del usuario.

El progreso no se oculta en un `<details>`: la pantalla mantiene visible el
mismo monitor de cinco etapas que generación (fuentes, histórico, evaluación,
composición y resultado), junto con el log vivo. La evaluación base registra
cada estrategia y la construcción registra cada posición añadida.

El resultado conserva una copia completa del perfil prop. Los perfiles son
datos versionados en `prop_profiles.py`; cuando una firma cambie reglas se
actualiza el perfil y la fecha, no se mueve lógica a UBS.

La curva sólo conoce PnL cerrado diario. Por eso daily loss, equity e intradía
son aproximaciones y la pantalla exige validar la composición en MT5 antes de
comprar una evaluación.

## Escrituras y ejecución

No escribe en memorias ni proyectos de agentes. Sólo persiste
`prop_experiment_settings.json` y `prop_experiment_last_result.json` junto a la
configuración runtime del manager. Tampoco copia operaciones: devuelve la
composición y separa visualmente `Seed` de `Candidata`. El puente entre demos y
cuenta de fondeo sigue siendo una unidad posterior.

Los endpoints viven bajo `/api/prop-experiment/` (`config`, `state`, `log`,
`settings`, `run`, `stop`). El servicio, motor, carga de seeds, perfiles, rutas
y tres estáticos son ficheros nuevos; los únicos contactos con el manager son
el registro del coordinador, dos delegaciones HTTP y el enlace de cabecera.

## Portafolio Prop y flujo en tres fases (2026-11-23)

La primera fase ya no se llama sólo «búsqueda»: su salida persistente es un
**Portafolio Prop**, con composición, broker de origen y lote por estrategia.
Las curvas del pool están normalizadas a 0,01 lotes, por lo que el lote guardado
es `unidades * 0,01`. La selección usa exclusivamente 2025-01-01 → 2026-06-30.

La fase 2 congela esa composición y ejecuta en el agente los dos modelos de la
comprobación manual existente —OHLC M1 y Every Tick— entre 2025-11-20 y
2026-11-20. El runtime sólo admite un lote común por trabajo, así que el manager
agrupa por broker y lote; no se añadió ni se portó criterio al fork del nodo.
Cada estrategia conserva las dos curvas para compararlas en la misma pantalla.

La exclusión de esta pantalla es local al Portafolio Prop: no escribe la
cuarentena UBS. Al arreglar, las supervivientes son obligatorias, sus lotes se
reconstruyen desde la unidad mínima y el buscador puede incorporar sustitutas.
La composición reparada vuelve a dejar la fase 2 pendiente: no puede saltar a
2020–2024 sin validar también las sustituciones en OHLC y Every Tick.

La fase 3 usa 2020-01-01 → 2024-12-31 y no optimiza: evalúa exactamente las
estrategias y unidades de la revisión que terminó la fase 2. Cada fase tiene su
botón manual. `auto_continue` encadena 1→2→3 y se detiene antes de la tercera si
alguna comprobación MT5 acabó fallida.

El Portafolio Prop no admite por defecto estrategias Grid: el checkbox
`include_grids` empieza desmarcado y cruza tanto `EnableGrid=true` como el
nombre del set. Activarlo no rehabilita curvas con drawdown cerrado cero. Éstas
se descartan siempre porque el motor diario no tiene una medida con la que
dimensionar su riesgo. La composición exige al menos tres estrategias, limita
cualquier estrategia al 50% de las unidades y no acepta propuestas cuyo
drawdown cerrado combinado supere la pérdida máxima del perfil.

El bankroll es **mensual**, no un saldo único para todo el histórico. El coste
editable de cada prueba se descuenta del mes en que empieza; al cambiar de mes
se repone el bankroll completo. Los intentos son consecutivos: mientras uno está
activo el calendario marca «Portafolio en curso», el día de resolución marca
«Pasada» o «Fallada» y, cuando el dinero del mes no alcanza para otra compra,
los días restantes indican «Bankroll agotado». El resultado conserva el
calendario, los totales y una fila por mes con compras, gasto, saldo y resultados.
Para la cuenta de 5.000 el valor inicial del campo es 52 €. La primera versión
usó por error 260 €; al cargar esos ajustes concretos se migran a 52 €, pero
cualquier otro coste introducido expresamente por el usuario se conserva.

## Plan de validación prop v1

Los dos documentos «Hello traders» aportan experiencias e hipótesis, no
instrucciones ni evidencia suficiente para comprar cuentas. La hipótesis de una
tasa mecánica cercana al 28–30% sirve sólo como baseline que el laboratorio debe
superar de forma repetible.

La primera cohorte ya configurada es FTMO 1-Step, FTMO 2-Step, The5ers Hyper
Growth, The5ers High Stakes y The Trading Pit CFD Prime 1-Phase. Sus reglas se
verifican de nuevo en la fuente oficial antes de cada compra; el resultado guarda
la copia del perfil que realmente simuló.

1. Cribado histórico: comparar cada fuente por separado y sus combinaciones con
   el mismo riesgo, muchos puntos de inicio y registro de rachas de fallos.
2. Fuera de muestra: congelar composición y multiplicadores, y repetir en un
   tramo temporal que no intervino en la búsqueda.
3. MT5 exacto: validar equity e intradía, rollover, spreads, swaps y margen. El
   PnL diario cerrado de la pantalla no autoriza una compra.
4. Lote real controlado: pocas evaluaciones, presupuesto máximo previo y ficha
   por intento con perfil, versión de cartera, resultado y primera regla rota.
5. Fondeo y payout: medirlos aparte. Aprobar una evaluación no demuestra que la
   composición sobreviva las reglas operativas ni alcance un retiro.
