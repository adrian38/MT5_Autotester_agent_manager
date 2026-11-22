# Un agente cerraba la terminal de otro (2026-11-22)

## El síntoma

La auditoría semanal del portafolio #2 de Vantage murió con:

```
No se pudo obtener el HTML nativo y el uso corre con una sola terminal, así que
no hay ninguna otra donde reintentarlo: MT5 principal: No se identificó de forma
unívoca la ventana MT5 de 34933859 en VantageMarkets-Live 10: el terminal no
tenía ninguna ventana abierta
```

Ese «no tenía ninguna ventana abierta» es el censo que se añadió esa misma
mañana en `lectura_nativa_sin_reintento.md`, puesto justo para contestar si en
el fallo había cero ventanas o dos. La respuesta fue **cero**, y con eso quedó
descartada la hipótesis de aquella nota: no era una ventana que tardara en
titularse ni dos instancias peleando.

## La causa

`_terminal_pids()` devuelve **todos** los `terminal64.exe` de la máquina:

```
Get-CimInstance Win32_Process -Filter "name='terminal64.exe'"
```

Sin ruta de instalación, sin broker y sin usuario. Y tres sitios lo usaban para
decidir qué cerrar, con el patrón «lo que ha aparecido desde que miré»:

- `_login_terminal` devolvía `self._terminal_pids() - before` como «las
  terminales que he lanzado yo», y el `finally` de `_extract_real` las cierra.
- `_verify_tester_terminals` hacía lo mismo con el pool del tester.
- `_export_isolated_native_report`, con los perfiles de respaldo.

En este equipo corren cuatro agentes (IC, AXI, RoboForex y Vantage), así que
ese conjunto incluye las terminales de los vecinos.

## La prueba

Las dos auditorías arrancaron en el mismo segundo, mismo turno del cron. Horas
locales; el Journal de cada terminal está en local y el estado de la auditoría
en UTC.

| Hora | RoboForex (`audit-186`) | Vantage (`audit-2`) |
| --- | --- | --- |
| 13:45:42 | Inicio, «Conectando la cuenta real» | Inicio, «Conectando la cuenta real» |
| 13:45:45.325 | | su terminal arranca |
| 13:45:45.938 | su terminal arranca | |
| 13:45:46 | login 77056129 autorizado | login 34933859 autorizado |
| ~13:46:01 | | empieza la exportación nativa |
| **13:46:23.145** | | **su terminal muere, `exit with code 0`** |
| **13:46:23.744** | **su terminal muere, `exit with code 0`** | |
| 13:46:24 | «Cuenta MT5 verificada… HTML nativo real_account» | |
| 13:47:03 | | «el terminal no tenía ninguna ventana abierta» |

RoboForex muestreó su `before` sobre las 13:45:44 —`_terminal_pids()` lanza un
PowerShell y tarda su medio segundo—, y la terminal de Vantage apareció 1,3 s
después, así que entró en su conjunto. Al terminar su extracción, su `finally`
mandó `taskkill /PID` a las dos: primero la de Vantage, 0,6 s después la suya.

Las dos salidas son **limpias** (`exit with code 0` + `shutdown with 0`), que es
lo que deja `taskkill` sin `/F`, no un crash.

## Por qué costó tanto verlo

- **El mensaje señalaba al sitio equivocado.** Decía que no encontraba la
  ventana, y el sitio donde se busca la ventana estaba bien: el proceso ya no
  existía.
- **Es intermitente por construcción.** Sólo pasa cuando dos extracciones se
  solapan y una termina mientras la otra exporta. El turno siguiente pasó.
- **El arreglo anterior lo tapó un poco más.** Al añadir la espera de 20 s por
  intento, el fallo pasó de instantáneo a tardar 62 s (`3 × 20 + 2 × 1`), que es
  exactamente la distancia entre el inicio de la exportación y el error. Esa
  aritmética es lo que fija la hora de inicio de la exportación y permite ver
  que la terminal murió **dentro** de la ventana de reintentos.
- **El grafo no podía enseñarlo.** El código que corre es
  `manager_node_runtime/` de cada agente, y además el culpable está en **otro**
  proyecto: ningún `trace_path` dentro de un agente llega al que lo cierra.

## El arreglo

Los tres sitios acotan a la instalación con la que están trabajando:
`_terminal_pids_for_path(path)`, que filtra por `ExecutablePath`. Si la ruta
viene vacía, el conjunto sale vacío y no se cierra nada, que es mejor que
cerrarlo todo.

`_terminal_pids()` se queda para lo único que le toca: esperar a que un PID
concreto desaparezca dentro de `_close_terminal_pids_gracefully`. Su docstring
lo dice ahora en el sitio donde se edita.

## Ninguna instalación está compartida, y el `broker` ya lo garantizaba

Primero conté que IC y AXI tenían habilitada la instalación de RoboForex y que
el filtro por ruta no los separaría. **Era falso**, y la corrección importa
porque es lo que decide si hace falta más arreglo:

`_terminal_profiles()` descarta antes que nada los perfiles cuyo `broker` no es
el del nodo, así que un `[Terminal.N]` de otro broker **nunca** llega a
`_login_terminal`, esté habilitado o no. Lo mismo dice `agent_terminals()` en
`node_settings.py`: «las otras pertenecen a otro agente de la misma máquina».
Por eso la pantalla Multiterminales tampoco las enseña, y por eso el único
reparto que cuenta es el real: una instalación por agente.

El `[Terminal.1]` de RoboForex que seguía declarado en los `ui_settings.ini` de
IC y de AXI era un resto de la máquina vieja, invisible y sin uso. Se borró el
2026-11-22 a petición del usuario; esos ficheros están en `.gitignore`, así que
no hay commit que lo registre.

## Lo que queda abierto

- **`[Paths] mt5_path` sigue apuntando a RoboForex en IC y en AXI.** Es el
  respaldo que usa `_terminal_profiles()` cuando el broker del nodo no tiene
  **ningún** perfil habilitado, así que hoy no se ejecuta; el día que alguien
  deshabilite el último perfil, el agente abriría la instalación del vecino en
  vez de fallar. `agent_terminals()` ya lo rechaza por su cuenta.
- **Sigue sin haber plan B con una sola terminal.** Si la terminal muere a
  mitad de la exportación, los dos intentos siguientes se gastan esperando una
  ventana que no puede volver: nadie la relanza. Eso no lo arregla este cambio.
