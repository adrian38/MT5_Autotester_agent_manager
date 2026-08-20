# Reinicio autocontenido del manager

El encabezado del panel tiene un botón `Reiniciar manager`. Su contrato es una
secuencia estricta y fail-fast, ejecutada desde la raíz del repositorio:

1. `git pull`
2. `git push`
3. `docker compose up -d --build manager`

Si un comando falla, no se lanza el siguiente. El estado queda en
`runtime/manager_restart.json` y la salida en `runtime/manager_restart.log`; la
API `GET /api/manager/restart` permite que la interfaz siga el trabajo y muestre
el error.

## Por qué hay un contenedor trabajador

Producción sirve el manager desde `mt5-autotester-manager`. Ejecutar Compose en
ese mismo proceso lo mataría durante el reemplazo, antes de que pudiera terminar
la operación. `ManagerRestartController` crea por eso un contenedor auxiliar a
partir de la imagen actual y con los mismos bind mounts. El auxiliar ejecuta la
secuencia y sobrevive porque Compose reconstruye exclusivamente el servicio
`manager`.

El auxiliar reutiliza mediante `docker inspect` las rutas de origen que conoce
el daemon para el repositorio, configuración, runtime y proyectos de agentes.
Esto es necesario en Docker Desktop: una ruta visible dentro del contenedor no
es automáticamente una ruta válida para crear los bind mounts del contenedor
nuevo.

El repositorio se monta desde Windows y su árbol de trabajo puede contener
finales CRLF aunque el índice de Git almacene LF. El trabajador aplica
`core.autocrlf=true` solo mediante el entorno de sus comandos Git; así el
contenedor Linux no confunde todos los ficheros con cambios locales y no se
modifica la configuración compartida del repositorio.

La imagen del manager incluye Git, GitHub CLI, Docker CLI y el plugin Compose.
El servicio monta el repositorio en `/workspace/manager-repo` y el socket de
Docker. El socket concede control del daemon y se monta únicamente porque esta
función lo necesita explícitamente.

## Autorización GitHub de una sola vez

El contenedor Linux no puede reutilizar Git Credential Manager de Windows. Antes
del primer `git push`, el trabajador comprueba `gh auth status`. Si todavía no
hay sesión, cambia a `authentication_required` y ejecuta el flujo web de
`gh auth login`: la interfaz muestra el log con el código de dispositivo y un
enlace a `https://github.com/login/device`.

La sesión resultante vive exclusivamente en el volumen Docker nombrado
`manager-git-auth`, montado en `/root/.config/gh`. Los trabajadores posteriores
heredan ese volumen y ejecutan `gh auth setup-git` antes del push, de modo que la
autorización solo se pide la primera vez. El volumen no forma parte del
repositorio ni de `runtime/`; no copiar tokens a ninguno de esos lugares.

El login tiene quince minutos para completarse. Si caduca o se cancela, el push
y Compose no se ejecutan y el estado explica que hay que volver a pulsar el
botón para obtener un código nuevo.

Este flujo solo actualiza el repositorio y el contenedor del manager. No llama a
los endpoints de reinicio de aplicaciones de los agentes ni modifica sus forks
en `manager_node_runtime/`.
