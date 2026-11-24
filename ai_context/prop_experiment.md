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
  cambiar un filtro actualiza la vista previa sin guardar los ajustes.
- Los símbolos sin contrato The5ers configurado son excepciones de las
  familias. El inventario descuenta sus sets y muestra el total general, por
  familia, por broker y por símbolo debajo de los selectores. Candidatas, seeds
  y manuales se filtran antes de abrir sus informes. Este descarte previsto
  queda fuera del monitor de avisos y sigue separado de los fallos de valoración.

`PortfolioSource.saved_portfolios()` devuelve un estado con la lista dentro de
`portfolios`, no la lista directamente. El primer recorrido de portafolios
manuales iteró las claves de ese estado y falló al terminar las seeds con
`'str' object has no attribute 'get'`. El cargador manual consume desde entonces
la clave `portfolios`, y el doble de prueba reproduce la forma real del contrato.

## Simulación

La búsqueda evalúa varias escalas de lote de cada estrategia elegible, hasta
el margen disponible. La base toma una representante por cada combinación de
aprobaciones y mediana de días que ninguna otra supera en ambos criterios.
Si empatan en esos dos valores, usa los desempates de la campaña. El tamaño
sale de los resultados. Cada base se cruza con todo el pool a varios lotes.
Esto permite encontrar complementos que rindieron poco por separado.

El resultado guarda las bases que realmente se usaron, su broker, símbolo,
lote y la matriz de Pearson sobre PnL diario OHLC en EUR. El calendario es
común y los días cubiertos sin cierres cuentan como cero. Una serie constante
deja el coeficiente sin valor. La correlación es diagnóstica y queda fuera
del ranking. Las ejecuciones antiguas siguen disponibles con su resultado.

El buscador refina varios puntos de partida añadiendo, quitando, sustituyendo
miembros y reajustando unidades. Es una búsqueda acotada y determinista.
Si se repiten los mismos ajustes y la composición anterior sigue siendo
elegible, se prueba también con retoques de lote antes de decidir. El buscador
devuelve la mejor de las propuestas evaluadas.
La ampliación vuelve a recorrer el pool entero desde una composición de varios
miembros. Prueba también añadir y reducir conjuntamente los lotes existentes,
y continúa más allá de una ampliación que pierde contra el mejor resultado.
El número de miembros queda fuera del ranking. El resultado guarda las
propuestas comparadas, viables y la mejor puntuación observada para cada tamaño.

### Motor V2 y reutilización de evidencia

El selector conserva V1 como valor por defecto para los ajustes anteriores.
V2 vive en `prop_experiment_engine_v2.py`. Mide lotes para todo el pool y
refina varias composiciones con cambios de volumen, sustituciones y estrategias
que aportan PnL en los días más débiles. Conserva como punto de partida la
última composición comparable si aún cabe y sus miembros siguen admitidos.
Ambos motores usan la misma campaña The5ers, con el número de aprobaciones
por delante de la mediana de días. V2 prepara los eventos diarios cuando la
campaña los necesita y reutiliza los eventos por estrategia y lote en la misma
búsqueda.

V2 guarda los informes OHLC normalizados y la lectura de `EnableGrid` en
`prop_experiment_evidence.sqlite3`, junto a los ajustes del manager. Reutiliza
cada archivo por ruta, tamaño y fecha de modificación. La casilla de
actualización se aplica a la siguiente construcción y fuerza una lectura de
los archivos que usa esa ejecución. Las admisiones, las cuarentenas y los
símbolos deshabilitados se consultan cada vez. La recomposición usa la versión
de la cartera guardada y la validación histórica de V2 reutiliza su propia
evidencia OHLC por ruta.

Medición del 2026-11-24 con informes reales de ICTrading y la ventana de fase 1.
Al preparar 85 estrategias, V2 pasó de unos 25 a unos 3 segundos al reutilizar
177 lecturas. En la búsqueda sobre 24 de esas estrategias y la cuenta de 5.000 €,
V1 aprobó 32 pruebas con mediana de 9 días y V2 aprobó 33 con mediana de 10 días.
La prueba controlada de `test_prop_experiment_v2.py` cubre un caso distinto en
el que V2 mejora ambas medidas.

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

La campaña procesa aperturas y cierres netos por hora del informe. Las órdenes
simultáneas comparten una comprobación de margen libre. La prueba empieza sin
posiciones arrastradas. Una pausa liquida la posición simulada a los últimos
precios observados y el siguiente día opera únicamente aperturas nuevas. La
equity se estima en los eventos y al rollover, con la última observación de
cada símbolo. El OHLC de origen aporta costes y horarios aproximados. Spreads,
costes, tiempos de servidor y equity tick a tick de The5ers requieren la cuenta
de destino. La fase 1 presenta la campaña, su duración y su coste mensual.

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
El lote The5ers y su equivalente en el broker representan la misma exposición.
Se parte del contrato y del paso de lote de cada lado. La plata del caso del
23-11-2026 usaba 1.000 onzas por lote en ICTrading y 5.000 en The5ers. En ese
caso, 0,01 en The5ers equivale a 0,05 en ICTrading. Con los 0,56 lotes de la
captura, una apertura a 117,685 exigía 13.180,72 USD a 1:25. El nocional EUR de
la ficha de ICTrading describía otro contrato y otro precio.

La unidad se escala sobre el volumen fijo observado. Los descartes por
contrato, valoración o volumen quedan registrados por broker y fuente. La
simulación usa capital nominal en EUR. El PnL de los informes en EUR entra
directamente y el de otras divisas se valora con la última cotización OHLC
disponible hacia EUR, directa o vía USD. El margen y el flotante se valoran
desde la divisa de cotización del símbolo. Cada cruce debe tener una observación
reciente. Si falta una cotización al abrir la ventana, los informes OHLC
anteriores sólo aportan precios FX observados hasta ese momento.
Para un cruce FX sin una cotización reciente de su divisa cotizada, el motor
puede usar la tasa a EUR de la divisa base dividida por el precio observado
de la propia operación. Así se reproduce el cierre AUDCAD del 08-04-2026
en AXI aunque el siguiente USD/CAD llegue 41 segundos después.
Las fichas de [The5ers](https://lp.the5ers.com/hub-assets-specifications/)
y [RoboForex ECN](https://roboforex.com/help/faq/trading-vocabulary/what-is-a-lot/)
son el respaldo del tamaño de contrato publicado. Las fichas del terminal de
origen prevalecen cuando están disponibles.

La selección usa exclusivamente aperturas y cierres del OHLC OOS del periodo
`[2025-01-01, 2026-05-21)`. La reproducción empieza el primer día de mercado,
2025-01-02; el corte 2026-05-21 es exclusivo y el último día de mercado exigido
es 2026-05-20. **Hasta el 2026-11-24 la ventana cerraba el 2026-06-01**, con
2026-05-29 como último día exigido, y eso descartaba por «cobertura OHLC
incompleta» a seeds cuyo informe terminaba sólo unos días antes: el eje exige
**todos** sus días de mercado, así que faltar uno basta para quedarse fuera.
Acortar la ventana al 20 de mayo las admite. Final Tick 6M decide la elegibilidad de las candidatas, pero su
curva Every Tick no se incorpora a la simulación de fase 1. Cada tramo lee su
informe OHLC directamente. Un IS solapado o ausente ya no descarta un OOS válido.
`seed_scores.report_path` lo escribe el lanzador de seeds UBS con modelo 1.
El histórico manual viene de la pasada generada para el portafolio manual o
de su tramo OHLC existente. Se descarta cualquier modelo distinto de OHLC
declarado en el informe.

La suma de márgenes máximos por día acota la búsqueda de lotes. Dos estrategias
cuyos picos caen en días distintos pueden compartir capital. El margen de
campaña se calcula con las posiciones abiertas, los precios
observados y el apalancamiento The5ers. Una cartera con órdenes rechazadas o
margen libre negativo observado queda fuera de la clasificación utilizable.
Se muestran el margen utilizado máximo y el margen libre mínimo de la campaña.

La fase 2 congela esa composición y ejecuta en el agente los dos modelos de la
comprobación manual existente —OHLC M1 y Every Tick— entre 2025-11-20 y
2026-11-20. El runtime sólo admite un lote común por trabajo. El manager
agrupa por broker y lote equivalente de origen para validar la misma exposición.
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
mes de inicio y el presupuesto se repone al cambiar de mes. Sólo hay una
prueba activa. Con aperturas y cierres verificables, la siguiente empieza en la
primera apertura posterior al bloque que aprobó la anterior. Tras un stop-out,
espera una hora desde el evento de pérdida y empieza en la primera apertura
desde ese momento, aunque cambie el día o el mes. Arranca con saldo nuevo y
excluye las operaciones que abrieron antes de esa primera apertura.
La simulación sobre agregados diarios reanuda al día siguiente. Esa misma
campaña alimenta el calendario y el resumen mensual.

La campaña llama al motor independiente `the5ers_hyper_growth_engine`. Una
pausa diaria del 3% termina el día pero no falla la cuenta; sólo el stop-out
estático del 6% cuenta como fallo. Cada celda del calendario muestra las
pruebas que estuvieron activas ese día. El final del histórico queda «en
curso» si la cuenta todavía está abierta. La búsqueda ordena primero por el
número total de pruebas pasadas, luego
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
