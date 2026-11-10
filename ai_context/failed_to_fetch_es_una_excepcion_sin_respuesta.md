# «Failed to fetch» es una excepción que se comió la respuesta: 2026-11-10

En la web del manager, un aviso rojo que sólo dice **«Failed to fetch»** no es
un fallo de red del navegador ni del proxy: casi siempre es **una excepción que
se escapó de una ruta del manager**.

`ManagerHandler` hereda de `BaseHTTPRequestHandler`, y lo que escapa de
`do_GET`/`do_POST` lo recoge `socketserver`: imprime la traza en el log del
contenedor y **cierra la conexión sin enviar nada**. El navegador no recibe ni
código ni cuerpo, así que `fetch` rechaza con el único mensaje que sabe dar
para una conexión muerta. La pantalla enseña `error.message` y el usuario ve
cuatro palabras que no dicen ni qué agente falló.

## De dónde salía

`manager_http.node_request` sólo captura `HTTPError`. Un agente que no contesta
dentro de su timeout —5 s por defecto, `node.get("timeout", 5)`— levanta
`TimeoutError`, y uno sin red, `URLError`. Esas dos suben enteras hasta el
despacho.

La captura estaba puesta **ruta a ruta**, y no en todas: una auditoría con AST
el 2026-11-10 contó **15 reenvíos al nodo sin captura de red, de 45**, en
`manager_portfolio_routes`, `manager_schedule_routes`,
`manager_live_audit_reports`, `manager_manual_portfolio`,
`portfolio_coordinator_saved`, `live_account_ingest` y
`live_audit_executions`. Con cuatro agentes —IC, AXI, RoboForex y Vantage— y
uno cualquiera ocupado con MT5, el aviso sale una y otra vez.

No es sólo la red. El mismo síntoma lo dio un valor no serializable colado en
un POST («Object of type MarginModel is not JSON serializable»), que está
escrito en `tests/portfolio/test_portfolio_margin_profiles.py`.

## La red de seguridad

`ManagerHandler._dispatch` envuelve ahora el despacho entero de GET y POST:

- `URLError`, `TimeoutError` y `ConnectionError` → `502` con
  `{"error": "El agente no respondio: ..."}`.
- cualquier otra excepción → `500` con su motivo.
- la traza sigue yendo al log, que es donde se diagnostica.

Para saber si aún queda algo que contestar hay un testigo, `_answered`, que
pone `send_response` —por donde pasan todas las respuestas, `send_error`
incluido—. **Se reinicia en cada `_dispatch`**: el mismo objeto atiende varias
peticiones seguidas en una conexión keep-alive, y sin reiniciarlo el fallo de
la segunda se daría por contestado con la respuesta de la primera.

Un fallo que llega después de la cabecera —el cliente que se va a mitad,
`BrokenPipeError`— ya no se puede contestar: sólo se registra.

## Para la próxima

- **Un «Failed to fetch» se diagnostica en el log del contenedor**, no en el
  navegador: `docker logs mt5-autotester-manager | grep -A20 Traceback`.
- Poner la captura en la ruta nueva sigue siendo lo correcto, porque el mensaje
  de la ruta sabe **qué** agente y **qué** operación falló; la red de seguridad
  sólo garantiza que nunca se pierda la respuesta entera.
- El proxy no lo tapa ni lo provoca: Caddy registra sus propios 502 en
  `docker logs mt5-autotester-proxy`, con `uri` y motivo.
