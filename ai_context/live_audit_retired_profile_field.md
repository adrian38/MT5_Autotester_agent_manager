# Un campo retirado del esquema vacía «Portafolios activos»

## Qué pasó (2026-10-24)

La versión de las abiertas sin enlazar (`02ffcd2`) añadió
`reviewed_open_positions` al perfil y lo escribió en **los catorce perfiles**
de AXI y RoboForex. La reversión (`111c317`) le quitó el sitio en
`DEFAULT_LIVE_AUDIT_PROFILE`, pero **no tocó el dato ya guardado**.

Mientras el manager siguió vivo con el código anterior no se notó nada. Al
reiniciarse, `LiveAuditSettingsStore._load_settings` volvió a leer el fichero:

```
normalize_live_audit_settings(profile)
  -> ValueError: Campos desconocidos: reviewed_open_positions
except ValueError:
    continue          # ← se descarta el NODO ENTERO, sin decir nada
```

Resultado: los dos nodos reales desaparecieron del almacén, `live_audit_ids`
quedó vacío y la portada dijo «No hay portafolios activos. Entra en Auditoría
desde Generación y marca "Este uso está operando en real"» — un mensaje que
invita a reconfigurar nueve portafolios que estaban perfectamente guardados.

## Por qué cuesta verlo

- **El síntoma no nombra la causa.** La portada no dice «no pude leer el
  fichero», dice «no hay nada configurado». Son cosas opuestas.
- **El retraso.** Entre el cambio de esquema y el síntoma pasa el tiempo que
  tarde el proceso en reiniciarse: horas o días.
- **El fichero está bien.** Abrirlo y ver los nueve perfiles con
  `live_trading: true` lleva a buscar el fallo en otra parte.
- **Una clave de más parece inofensiva.** Lo era mientras nadie releyera.

## El contrato de ahora

`LiveAuditSettingsStore._without_retired_fields` olvida al **leer** los campos
que el esquema ya no conoce, y los usos sobreviven. Al **escribir** por la API
se siguen rechazando: ahí una clave desconocida es una errata del cliente y hay
que cantarla. Lo guarda `tests/test_live_audit_retired_profile_fields.py`, con
una prueba que sin el arreglo falla dejando el nodo vacío.

Queda en pie, y es la parte que no se arregló: `except ValueError: continue`
sigue descartando el **registro completo** del nodo ante cualquier otro error
de validación, y sigue sin decirlo. Un perfil corrupto esconde a los otros
seis.

## Reglas al cambiar el esquema del perfil

1. **Quitar un campo del esquema es una migración de datos**, no sólo una
   edición de código. Revisar `runtime/runtime/live_audit_settings.json` antes
   de dar por terminada una reversión que toque `DEFAULT_LIVE_AUDIT_PROFILE`.
2. **Revertir una entrega que escribió en el perfil deja rastro.** El commit
   se revierte; el fichero del usuario, no.
3. Ante «no hay portafolios activos» con el fichero lleno, cargarlo a mano:
   `LiveAuditSettingsStore(ruta).state(nodo)` — si devuelve cero perfiles, el
   problema es de validación, no de configuración.
