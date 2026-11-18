"""El candado que impide lanzar un sondeo encima de la recogida del anterior.

El agente publica **una** tanda y su `account_snapshot_artifact` sólo sirve
los informes de la visible. Mientras el manager los baja, las terminales ya
están sueltas: el agente contesta que su `is_running` es falso y que su
trabajo está en `idle`, así que preguntarle no protege nada. Lanzar ahí
sustituye el estado visible y los informes que faltaban por bajar dejan de
existir a media recogida; `live_account_ingest._merge_accounts` recibe un 404
por cada uno y los cuenta como cuentas que no se dejan leer, que es lo
contrario de lo que pasó.

Pasó el 2026-11-18 a las 10:41. El manager acababa de reiniciarse con
`live_account_report.SERIES_VERSION` cambiada, así que estaba recogiendo otra
vez las tandas que los agentes seguían guardando; el usuario pulsó «relectura
ahora» y la de RoboForex se cerró con tres cuentas archivadas y
`77056129@RoboForex-ECN: El nodo devolvió 404 al leer
account_77056129_RoboForex-ECN.html`.

Vive en su propio módulo porque no es de nadie: lo echa la vuelta del cron
(`live_account_watch.tick`) y lo miran los dos botones, que están en
`live_account_wealth_watch` y en `manager_account_routes`. No importa nada
del paquete, así que está en el suelo de la pila y no puede crear ciclos.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

_COLLECTING: set[str] = set()
"""Los nodos cuya tanda está bajando este proceso **ahora mismo**.

De este proceso a propósito, y no el `manager_status` que `collect` persiste
para la barra de la pantalla: un manager que se reinicia a media recogida
dejaría ese campo en «recogiendo» para siempre y el candado no volvería a
abrirse nunca. Lo que muere con el proceso es justo lo que hay que mirar,
porque al arrancar no se está recogiendo nada.
"""

COLLECTING_REASON = "el manager todavía está recogiendo el sondeo anterior"
"""Lo que se le dice al usuario cuando el candado está echado.

En minúscula y sin punto porque las dos pantallas lo pintan detrás del nodo
(`<nodo>: <motivo>`), igual que los motivos de `wealth_watch.node_busy`.
"""


@contextmanager
def collecting(node_id: str) -> Iterator[None]:
    """Echa el candado de ese nodo mientras dure la recogida.

    El `finally` no es cortesía: una recogida que revienta ya no paraba la
    vuelta del cron, y dejar el candado echado condenaría a ese nodo a no
    sondearse nunca más hasta reiniciar el manager, que es peor que la
    carrera que esto viene a cerrar.
    """
    _COLLECTING.add(node_id)
    try:
        yield
    finally:
        _COLLECTING.discard(node_id)


def collecting_reason(node_id: str) -> str:
    """Por qué no se puede lanzar en ese nodo ahora mismo, o cadena vacía.

    Las dos citas de cron no lo necesitan: `live_account_watch.tick` recoge y
    lanza nodo por nodo, y la vuelta del patrimonio va detrás en ese mismo
    hilo (`ManagerServer.live_account_thread`). Esto es para los dos botones,
    que entran por un hilo de HTTP mientras la vuelta corre.

    Quien lo consulte después de hablar con el agente tiene que hacerlo **lo
    último**: esa conversación tarda hasta quince segundos y la recogida
    puede empezar dentro de ellos.
    """
    return COLLECTING_REASON if node_id in _COLLECTING else ""
