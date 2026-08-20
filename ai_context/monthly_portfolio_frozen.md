# Portafolio UBS mensual congelado

## Decisión vigente desde 2026-08-20

El Portafolio UBS mensual queda temporalmente fuera de desarrollo. A partir de
ahora no se debe corregir, ampliar, refactorizar ni sincronizar su interfaz,
JavaScript, orquestación o algoritmo salvo que el usuario pida explícitamente
trabajar en el ámbito mensual.

Su botón principal de generación permanece deshabilitado en
`mt5_manager/static/portfolios_monthly.html` y su manejador JavaScript no envía
la acción `generate`. La entrada «Portafolio mensual» de las tarjetas del panel
principal también permanece deshabilitada. Las consultas y los portafolios mensuales ya guardados se
mantienen visibles; esta decisión no borra datos ni elimina el motor existente.

Las mejoras del Portafolio UBS normal deben limitarse a `full_history`. Si una
primitiva estable compartida necesitara cambiar por una petición futura, hay que
señalar el impacto mensual antes de hacerlo y obtener una petición explícita si
ese impacto altera su comportamiento.

## Excepción

Solo una petición explícita del usuario para reactivar o modificar el
Portafolio UBS mensual levanta esta congelación para el alcance solicitado.
