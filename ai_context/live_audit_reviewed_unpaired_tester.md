# Validar una fila «SIN REAL» una a una

## El problema

La portada («Portafolios activos») avisa bajo *Operaciones sin enlazar* de dos
cosas, contadas aparte porque son problemas distintos: las reales que nadie
explicó y **las del tester que la cuenta no ejecutó**. Las primeras tienen su
gesto en Análisis —el Excel de atribución—; las segundas no tenían ninguno
propio. Lo único que las callaba era validar la estrategia entera en *Demo
contra real*, que dice algo mucho más fuerte: «ya miré los números de esta
estrategia».

Medido el 2026-10-24 contra el manager que corre en Docker (`localhost:8750`) y
los perfiles reales de AXI y RoboForex:

| Tarjeta | Filas SIN REAL | Estrategias validadas | Lo que pedía la portada |
| --- | --- | --- | --- |
| #109 AXI (`audit-109`) | 23 | 13 | 1 |
| #105 AXI (`audit-105`) | 2 | 6 | 2 |
| #173 Robo (`audit-173`) | 2 | 9 | 1 |
| #170 Robo (`audit-170`) | 1 | 1 | 0 |

El número que importa no es el que queda, sino el que se fue: en `audit-109`,
**veintidós de veintitrés dejaron de pedirse por el veredicto de su estrategia,
no porque nadie las abriera**. Y «reales sin explicar» estaba a cero en los
nueve usos: el Excel ya las había atribuido todas.

## Lo que hace

La fila «SIN REAL» de la pantalla de operaciones lleva su propia casilla
**Validada**, al lado del selector de emparejado a mano y no en vez de él,
porque son dos respuestas distintas a la misma pregunta:

- **Emparejar a mano** (`manual_pairs`) le pone real a la fila y la vuelve a
  medir con las tolerancias de siempre: **cambia el veredicto**.
- **Validada** (`reviewed_unpaired_tester`) sólo dice «ya miré ésta»: no
  reanaliza nada, no entra en ninguna curva y lo único que cambia es que la
  portada deja de contarla.

Sale también en la fila que el usuario declaró sin real a mano: esa decisión
dice que no hay real que buscar, no que la fila esté mirada.

## Dónde se ve

En **Análisis**, sección «CUADRE · EJECUTADAS», debajo de la tabla de reales sin
dueño y con la misma forma: una fila por operación —símbolo, lote, apertura,
cierre, PnL del tester, por qué no tiene real— y la casilla «Validada» al final.
La pantalla de operaciones lleva además la misma casilla en su fila «SIN REAL»,
al lado del selector de emparejado a mano, porque es ahí donde se decide si la
real existe. Las dos escriben el mismo campo del perfil, así que lo validado en
una sale marcado en la otra.

## La huella, no la fila

La marca se guarda contra `tester_pair_key`, la misma huella que las parejas
manuales: `estrategia|símbolo|lado|apertura|cierre`. El Strategy Tester no
exporta ticket y el `#T6` de la pantalla es **la posición en la lista**, que se
mueve en cuanto la auditoría se vuelve a ejecutar. Con la huella, lo validado
sigue validado al reanalizar el uso.

Hay tres copias de esa expresión —`live_audit_manual_pairs.py`,
`live_audit_result.js` y `live_audit_reproduction.js`— y lo único que las ata
es el texto: si una se separa, la portada cuenta como pendiente lo que la
pantalla da por validado. Lo vigila
`test_the_screen_and_the_card_build_the_same_fingerprint`.

Comprobado sobre la materia prima archivada de `audit-109`: las 23 filas dan 23
huellas distintas. Una colisión silenciaría dos filas con un clic.

## Por qué vive en el perfil del uso

Igual que las estrategias validadas y el escalado de la vista: en el perfil y
no en `localStorage`, porque la portada tiene que contar lo mismo desde
cualquier dispositivo. Es campo **sólo del manager**: viaja en
`MANAGER_ONLY_PROFILE_KEYS` y lo excluye `run_payload`, así que no llega al
nodo. Sin veredicto guardado, al revés que `reviewed_strategies`: una fila sin
real no se mide, está o no está, así que la marca es sólo la fecha.

## Cómo se comprobó la pantalla

No hay runtime de JavaScript en este equipo, así que además del port medido en
Python se ejecutó la pantalla de verdad: copia de `live_audit_result.html` y
sus `.js` al scratchpad con las rutas en relativo, un `stub.js` que sustituye
`window.fetch` con la materia prima archivada de `audit-109`, y
`python -m http.server` abierto en el navegador integrado. Allí se vio lo que
los `assertIn` no pueden ver: 23 casillas, el `POST` con la huella correcta, la
marca de vuelta tras recargar y la casilla deshecha con su mensaje cuando el
guardado falla.
