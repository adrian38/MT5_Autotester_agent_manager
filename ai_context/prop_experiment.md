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
