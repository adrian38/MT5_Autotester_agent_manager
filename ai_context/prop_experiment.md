# Experimenta Prop: tres fuentes para aprobar evaluaciones

Pantalla aislada `/prop_experiment.html`. Su objetivo no es maximizar retorno
ni crear un portafolio UBS: busca una composición cuya simulación histórica
resuelva y apruebe el mayor número de evaluaciones consecutivas en el período,
con el menor tiempo de aprobación posible y respetando sus reglas y bankroll.

## Pool y brokers

- Lee ICTrading, AXI y RoboForex mediante el mismo `readonly_source` de
  Experimenta. Vantage se muestra, pero queda excluido por el valor estable
  `portfolio_broker == VANTAGE` tanto en la interfaz como en el backend.
- La fase 1 usa tres fuentes fijas: candidatas actuales que conservan las cuatro
  etapas aceptadas de `candidate_rows`, incluido Final Tick 6M; seeds UBS que
  tienen aceptada su fase UBS Final Tick 6M; y estrategias de portafolios
  manuales, que son la única excepción sin fases.
- En memorias que tienen la columna de veredicto, Final Tick 6M exige las dos
  condiciones: `status='accepted'` y `accepted=1`. Haber creado o terminado la
  fila de la fase no basta para entrar en el conteo ni en el pool.
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
- El resultado muestra el total utilizable por broker y su desglose entre
  generadas FT6M, seeds UBS FT6M y manuales. Es el pool final después de
  cuarentena, familias, deduplicación, credibilidad y cobertura OHLC de fase 1;
  los brokers seleccionados sin estrategias válidas aparecen con cero.
- Ese resumen no espera a que termine una búsqueda: al abrir la pantalla llama
  a `pool-inventory` para mostrar enseguida las admisiones por broker, sin abrir
  miles de informes. Cuenta fases, familias, símbolos deshabilitados, cuarentena
  y deduplicación. La fase 1 valida después credibilidad y cobertura OHLC;
  guardar ajustes vuelve a calcular el inventario visible.

`PortfolioSource.saved_portfolios()` devuelve un estado con la lista dentro de
`portfolios`, no la lista directamente. El primer recorrido de portafolios
manuales iteró las claves de ese estado y falló al terminar las seeds con
`'str' object has no attribute 'get'`. El cargador manual consume desde entonces
la clave `portfolios`, y el doble de prueba reproduce la forma real del contrato.

## Simulación

La búsqueda evalúa varias escalas de lote de cada estrategia elegible, hasta
el margen disponible. Con las 24 mejor puntuadas siembra composiciones
individuales y parejas. Refina varios puntos de partida añadiendo, quitando,
sustituyendo miembros y reajustando unidades. Es una búsqueda acotada y
determinista. La composición devuelta es la mejor de las propuestas evaluadas.
En fase 1 también cruza las dos mejores bases individuales, a lote bajo y
medio, con el pool entero. Esto descubre estrategias que aportan días de
beneficio donde la base está inactiva y quedan abajo en el ranking individual.

El simulador exige cobertura de la ventana con las fechas declaradas de las
pasadas que construyeron la serie. Un día laborable cubierto sin cierres
consume tiempo y conserva el saldo. Si falta un tramo de evidencia, la
estrategia queda fuera y el resultado informa cuántas quedaron excluidas.

El progreso no se oculta en un `<details>`: la pantalla mantiene visible el
mismo monitor de cinco etapas que generación (fuentes, histórico, evaluación,
composición y resultado), junto con el log vivo. La evaluación base registra
cada estrategia y la construcción registra cada posición añadida.

El resultado conserva una copia completa del perfil prop. Los perfiles son
datos versionados en `prop_profiles.py`; cuando una firma cambie reglas se
actualiza el perfil y la fecha, no se mueve lógica a UBS.

La campaña procesa cierres netos por hora de reporte. Los cierres con misma
marca de tiempo se agrupan. La prueba empieza sin posiciones arrastradas y,
tras una pausa, el modelo deja de incorporar cierres de posiciones anteriores
al siguiente día. Al tocar una barrera de pérdida se usa el saldo de la
barrera. Esta aproximación carece del flotante tick a tick, del rollover real
y de la liquidación a precio de mercado. La fase 1 no muestra la curva
acumulada de la composición: el objetivo se presenta mediante la campaña de
pruebas, su duración, sus resultados y el consumo mensual de bankroll.

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
Cada broker aporta sus propios mínimos de volumen y nocionales. Para símbolos
sin mínimo medido se usa el menor volumen positivo observado en la pasada.
Un informe a lote fijo se reescala al mínimo disponible y el margen se estima
con los tramos de la prop. La fase 2 recibe exactamente el lote calculado.
La selección usa exclusivamente los cierres del OHLC OOS del periodo
`[2025-01-01, 2026-06-01)`. La reproducción empieza el primer día de mercado,
2025-01-02; el corte 2026-06-01 es exclusivo y el último día de mercado exigido
es 2026-05-29. Final Tick 6M decide la elegibilidad de las candidatas, pero su
curva Every Tick no se incorpora a la simulación de fase 1. Para cada
estrategia, el margen por unidad se multiplica por su máximo de posiciones
simultáneas observado en ese OHLC; la suma conservadora de esos picos no puede
superar el capital de la prueba con el apalancamiento del perfil prop.

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
nombre del set. Activarlo sólo cambia ese filtro; no añade límites inventados de
número de estrategias, concentración o drawdown cerrado.

El bankroll es **mensual**, no un saldo único para todo el histórico. La propia
búsqueda usa `simulate_monthly_campaign`: el coste editable se descuenta del
mes de inicio, nunca se reutiliza un día entre intentos y el presupuesto se
repone al cambiar de mes. Sólo hay una prueba activa: tras resolverla, la
siguiente empieza en el próximo día histórico disponible si su mes conserva
bankroll. Esa misma campaña alimenta el calendario y el resumen mensual.

La campaña llama al motor independiente `the5ers_hyper_growth_engine`. Una
pausa diaria del 3% termina el día pero no falla la cuenta; sólo el stop-out
estático del 6% cuenta como fallo. Cada celda del calendario representa la
prueba que está activa ese día; el final del histórico queda «en curso», no
fallado. La búsqueda ordena primero por número total de pruebas pasadas, luego
por menor mediana de días naturales para aprobar y después por tasa de aprobación: una
cartera lenta con 100% no vence a otra que pasa más pruebas en el mismo tramo.
En la tabla mensual, las compras se cuentan en su fecha de inicio, los
resultados en su fecha de cierre y las cuentas en curso al cierre del mes.
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
