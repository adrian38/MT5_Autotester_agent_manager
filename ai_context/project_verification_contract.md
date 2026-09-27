# Contrato único de verificación

`python -m tools.verify_project` es la puerta final del repositorio. Sincroniza
las reexportaciones UBS y ejecuta las auditorías de nombres, tamaños e índice,
las guardas arquitectónicas, el suite completo del manager y, si la copia IC
está montada, sus pruebas focales del pipeline guiado. `--quick` sirve durante
la iteración y omite únicamente el suite completo del manager.

El comando captura `HEAD` y el estado tracked del manager y de IC antes de las
pruebas. Si otra sesión cambia alguno durante la medición, el resultado falla:
una prueba verde ya no describe necesariamente el código que se va a entregar.
Los ficheros sin seguimiento no se comparan para que el propio trabajo nuevo no
invalide la medición; aun así deben revisarse en el diff final.

## Importaciones estrella

`tools.undefined_names` no puede demostrar qué nombres aporta un `import *`.
Por eso sólo admite las seis fachadas históricas de auditoría enumeradas en
`ALLOWED_STAR_IMPORTS`. `tests/test_undefined_names.py` exige igualdad exacta:
una importación estrella nueva y una excepción que ya no se use fallan.

## Ramas, espejos y pruebas

La matriz de `AGENTS.md` define qué checkout es escribible en cada rama. La
rama nunca amplía el objetivo del usuario. Los dos ficheros guiados de
producción son espejos byte a byte y requieren commits hermanos cuando cambian;
las pruebas específicas de manager o IC no son espejos y pueden corregirse en
el repositorio que contiene su contrato.

La entrega debe identificar commits, pruebas y recuentos, copias omitidas,
acciones de porting o reinicio y el estado de cada checkout. Si la memoria de
código no estuvo disponible, se declara la limitación y nunca se usa una
búsqueda negativa del grafo para afirmar que algo no existe.
