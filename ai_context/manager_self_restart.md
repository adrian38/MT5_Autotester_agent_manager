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

La imagen del manager incluye Git, Docker CLI y el plugin Compose. El servicio
monta el repositorio en `/workspace/manager-repo` y el socket de Docker. El
socket concede control del daemon y se monta únicamente porque esta función lo
necesita explícitamente.

Git se ejecuta sin prompt interactivo. La autenticación de `git push` debe estar
disponible en el entorno donde corre el trabajador; si no lo está, el push falla,
no se reconstruye el manager y el motivo queda en el log. No guardar tokens ni
credenciales en este repositorio.

Este flujo solo actualiza el repositorio y el contenedor del manager. No llama a
los endpoints de reinicio de aplicaciones de los agentes ni modifica sus forks
en `manager_node_runtime/`.
