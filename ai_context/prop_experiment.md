# Experimenta Prop: candidatos más seeds para aprobar evaluaciones

Pantalla aislada `/prop_experiment.html`. Su objetivo no es maximizar retorno
ni crear un portafolio UBS: busca una composición cuya simulación histórica
apruebe una evaluación prop con sus límites de pérdida y objetivo.

## Pool y brokers

- Lee ICTrading, AXI y RoboForex mediante el mismo `readonly_source` de
  Experimenta. Vantage se muestra, pero queda excluido por el valor estable
  `portfolio_broker == VANTAGE` tanto en la interfaz como en el backend.
- Las candidatas actuales siguen viniendo de `candidate_rows`, por lo que
  conservan el contrato de las cuatro etapas aceptadas.
- Además lee `seed_scores` de cada broker con `active=1`, `accepted=1` y
  `status='accepted'`. Una seed es la estrategia original del broker, no el
  parámetro aleatorio de una búsqueda. Su HTML MT5 se convierte a
  `RobustStrategySet` únicamente para reutilizar el alineado de curvas.
- Una seed duplicada por ruta con una candidata se elimina sólo de la parte
  seed; nunca sustituye ni altera el pool actual.

## Simulación

Cada estrategia se normaliza para que una unidad represente
`risk_unit_pct` del capital según su valley drawdown. Los trials empiezan en
distintos días históricos, siempre que quepa la duración completa de todas las
fases. Se comprueban objetivo, pérdida diaria, pérdida máxima estática o
trailing EOD, días mínimos, días rentables y consistencia.

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
