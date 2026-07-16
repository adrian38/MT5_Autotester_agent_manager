# MT5 Autotester Agent Manager

MVP distribuido para iniciar generaciones UBS y observar su estado desde un
único panel. Está pensado para el caso real de este entorno: tres brokers,
varios usuarios Windows y dos PC dentro de la misma red local.

Cada copia del autotester conserva sus rutas, MT5, multiterminales y memoria
SQLite. El manager coordina las ejecuciones y contiene la interfaz y el motor
de Portafolio UBS y Portafolio UBS mensual. El guardado de portafolios pasa
siempre por la API autenticada del nodo propietario, incluso si hoy comparte
equipo con el manager; una generación en curso no se reinicia.

## Arquitectura

```text
Panel central (puerto 8750)
          |
          +-- HTTP + token --> Usuario/PC RoboForex : nodo 8761 -> ubs_agent.py
          +-- HTTP + token --> Usuario/PC ICTrading : nodo 8761 -> ubs_agent.py
          +-- HTTP + token --> Usuario/PC AXI       : nodo 8762 -> ubs_agent.py
                                                        |
                                                        +-> ui_settings.ini
                                                        +-> MT5 de ese usuario
                                                        +-> SQLite de ese broker/cuenta
```

Es importante ejecutar cada nodo dentro del usuario Windows que posee las
instalaciones MT5. Un servicio Windows en otra sesión no puede controlar de
forma fiable los terminales gráficos del usuario.

## Qué incluye el MVP

- Inicio remoto de generaciones con generaciones, variantes, semillas, modo,
  fechas, ejecución real o `dry-run`.
- Selección por nodo de modo `production`/`discovery`, límite de terminales
  MT5 y pipeline opcional Robustez OOS -> Final Tick -> Final Tick 6M.
- Cola FIFO independiente por nodo para generaciones y reparaciones. Las tareas
  pendientes se conservan en `runtime/<node_id>/queue.json`, arrancan
  automáticamente y pueden quitarse desde la tarjeta antes de comenzar.
- Estado de conexión, PC/usuario, PID, resultado y timestamps.
- Último run SQLite y conteos por estado de generación, robustez, Final Tick y
  Final Tick 6M.
- Log remoto de las últimas líneas y detención del proceso.
- Actualización automática del panel cada 5 segundos.
- Autenticación con un token distinto por nodo.
- Solo biblioteca estándar de Python; no requiere instalar FastAPI/Flask.

## Integración con la aplicación de cada broker

Las ramas actuales pueden alojar el nodo dentro del propio `app_ui.py` mediante
`manager_node_lifecycle.py`. En ese modo no se ejecuta `run_node.bat`: abrir
MT5 Autotester inicia el servidor HTTP en un hilo interno y cerrar la app lo
detiene. Si existe una generación remota activa, la app pide confirmación antes
de cerrarse.

El Manager central existe una sola vez, únicamente en el equipo de control.
No se copia el proyecto `MT5_Autotester_agent_manager` a los equipos broker.

Cada copia de MT5 Autotester contiene solamente `manager_node_runtime/` y debe
tener su propio `manager_node.json` junto a `app_ui.py`. Ese JSON es local,
contiene el secreto y no se versiona. Se puede crear copiando
`manager_node.example.json`. La integración solo arranca
si el `project_dir` del JSON coincide exactamente con el proyecto de la app
abierta, lo que impide que una rama levante por error el nodo de otro broker.

Para usar un nombre o ubicación diferente también se puede añadir a
`ui_settings.ini`:

```ini
[ManagerNode]
enabled=1
config_file=C:\ruta\configuracion-especifica\node.json
```

`run_node.bat` queda únicamente como herramienta de diagnóstico o como
compatibilidad para una rama que todavía no tenga integrada esta clase.

## Instalación manual/fallback en cada usuario

Se puede copiar este proyecto completo a ambas PC o compartirlo como repositorio.
En cada usuario que ejecuta un broker:

1. Copiar `config/node.example.json` a `node.json`.
2. Generar un secreto desde PowerShell:

   ```powershell
   .\tools\new_token.ps1
   ```

3. Editar `node.json`:
   - `node_id`: identificador único.
   - `project_dir`: copia local de `MT5_Autotester_agent` de ese usuario.
   - `broker` y `account_type`: exactamente los usados por el autotester.
   - `port`: debe ser diferente si dos usuarios/nodos comparten la misma PC.
   - `token`: secreto generado en el paso anterior.
4. Si la rama aún no integra el nodo, ejecutar `run_node.bat` dentro de ese
   usuario. Con la integración activa basta abrir la aplicación MT5 Autotester.

El nodo reutiliza `ui_settings.ini`. Esto incluye directorio de seeds, salida,
plantilla, criterios, fechas por defecto, terminal principal o configuración
multiterminal y rutas MT5. Los campos enviados desde el panel solo sustituyen
los parámetros de la nueva generación.

El nodo también detecta automáticamente las opciones disponibles en el
`ubs_agent.py` de cada rama. La rama legacy de RoboForex no recibe flags nuevos
que no reconoce y, si existe, usa `outputs/ubs_memory.sqlite`; las ramas
multibroker usan `outputs/ubs_memory_<BROKER>_<ACCOUNT>.sqlite`. Se puede forzar
cualquier ruta con `memory_path` en `node.json`.

### Firewall de Windows

En cada PC hay que permitir únicamente los puertos de sus nodos en el perfil
de red privada. Ejemplo desde PowerShell como administrador:

```powershell
New-NetFirewallRule -DisplayName "MT5 Manager nodo 8761" `
  -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8761 `
  -Profile Private -RemoteAddress LocalSubnet
```

No se recomienda exponer estos puertos a Internet. Los tokens protegen las
órdenes, pero el diseño está orientado a una LAN confiable.

### Inicio automático por usuario

Cuando el despliegue manual funcione, crear un acceso directo a `run_node.bat`
en `shell:startup` dentro de cada usuario. Así el nodo arranca al iniciar sesión,
que es también cuando MT5 puede ejecutarse en esa sesión interactiva.

## Configuración del panel central

1. Copiar `config/manager.example.json` a `manager.json`.
2. Reservar IP fijas en el router o usar nombres DNS locales para las dos PC.
3. Configurar en cada entrada de `nodes` la URL y el mismo token de su
   `node.json`.
4. Para habilitar los portafolios centrales de un nodo local, configurar
   `portfolio_project_dir`, `portfolio_broker` y `portfolio_account_type`.
5. Ejecutar `run_manager.bat`.

Las configuraciones de ambos constructores se guardan por nodo y tipo en
`runtime/portfolio_settings.json`. Generar, completar o reoptimizar una propuesta
solo lee SQLite y los reportes hasta que el usuario confirma **Guardar** o
**Aplicar**. Guardar, poner en cuarentena, deshacer, borrar y reintegrar usan
transacciones cortas compatibles con la app original. Antes de aplicar una
recomposición se guarda una versión recuperable.

Al guardar una propuesta, el manager envía el paquete completo al nodo y solo
lo da por guardado cuando el nodo confirma el ID escrito en su SQLite local.
No existe una ruta alternativa de escritura directa para proyectos que estén
en el mismo equipo; por eso manager y nodo deben ejecutar una versión compatible.

El constructor combina automáticamente las memorias del broker que existan en
`outputs`: RoboForex `ECN/PRO`, AXI `STANDARD/PREMIUM` e ICTrading `STANDARD`.
Si las memorias están en ubicaciones no estándar, se puede añadir al nodo
`portfolio_memory_paths`, con objetos `{"account_type": "...", "path": "..."}`.
Los cálculos dejan trazas en `portfolio_logs`, y reutilizan la configuración
Telegram del proyecto del broker para avisar al terminar, fallar o guardar.

El navegador abre `http://127.0.0.1:8750`. Por defecto el panel solo escucha en
el equipo central. Para abrir el panel desde otros equipos, cambiar su `host` a
`0.0.0.0` y añadir una regla de firewall para el puerto 8750.

## Ejecución desde consola

```powershell
python -m mt5_manager.node --config node.json
python -m mt5_manager.manager --config manager.json
```

## Manager central con Docker

Dockeriza únicamente el manager central. Los nodos y MT5 continúan ejecutándose
en la sesión Windows interactiva de cada broker; no deben entrar en el
contenedor.

1. Mantener el `manager.json` local ya configurado con las IP y tokens reales.
   Compose lo monta como solo lectura; nunca se copia dentro de la imagen.
2. Crear el archivo de rutas para Docker:

   ```powershell
   Copy-Item .env.docker.example .env
   ```

3. Editar `.env` con la carpeta IC local y las credenciales SMB del PC que
   comparte `F` y `G`. Docker Desktop no hereda las unidades de usuario
   `X:`/`Y:`; Compose monta directamente ambos recursos mediante CIFS. El
   archivo `.env` está ignorado por Git y no entra en la imagen.
4. Construir y arrancar:

   ```powershell
   docker-compose up -d --build
   docker-compose ps
   ```

5. Abrir desde otro equipo de la LAN:

   ```text
   http://IP-DEL-PC-CENTRAL:8750
   ```

El arranque genera en `runtime/manager.docker.json` una copia adaptada: escucha
en `0.0.0.0`, traduce las carpetas a `/data/*` y convierte automáticamente las
URLs locales `127.0.0.1`/`localhost` a `host.docker.internal`. El volumen
`./runtime` conserva preferencias, configuraciones y colas entre reinicios.
Los tres proyectos se montan para que el motor central pueda leer memorias y
reportes y mantener las operaciones existentes. No se incluyen `manager.json`,
tokens ni datos de ejecución dentro de la imagen.

En Docker, **Exportar sets** genera un ZIP y lo descarga en el navegador del
equipo que está usando el panel. En ejecución Windows local se mantiene el
selector nativo de carpetas.

Para limitar el panel a la red privada, crear una regla de firewall solo para
la subred local. No se debe publicar el puerto 8750 en Internet: el panel del
manager no tiene autenticación de usuario y permite iniciar o detener tareas.

```powershell
New-NetFirewallRule -DisplayName "MT5 Manager web 8750" `
  -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8750 `
  -Profile Private -RemoteAddress LocalSubnet
```

## API del nodo

Todas las rutas requieren `Authorization: Bearer <token>`.

| Método | Ruta | Uso |
|---|---|---|
| `GET` | `/api/v1/health` | Salud básica |
| `GET` | `/api/v1/status` | Proceso y snapshot SQLite |
| `GET` | `/api/v1/logs?lines=200` | Cola del log |
| `POST` | `/api/v1/jobs/generation` | Iniciar generación |
| `POST` | `/api/v1/jobs/stop` | Detener generación |
| `POST` | `/api/v1/portfolios/save` | Guardar una propuesta en la SQLite local del nodo |

## Pruebas

```powershell
python -m compileall -q mt5_manager tests
python -m unittest discover -s tests -v
```

Las pruebas no abren MT5 ni modifican las memorias reales.

## Siguiente alcance previsto

Este primer corte cubre iniciar generaciones y ver estados. La API deja la
separación necesaria para añadir después robustez, Final Tick, colas
programadas, notificaciones y comparación consolidada entre brokers sin tener
que rediseñar la comunicación entre PC.
