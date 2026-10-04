"""Esperas asincronas compartidas por los tests.

Un plazo justo no prueba nada y convierte una maquina ocupada en un fallo rojo
que no dice nada del codigo. Estos tests esperan a que un hilo o un job termine;
como toda espera sale en cuanto se cumple la condicion, un plazo holgado no
alarga la pasada normal, solo alarga el caso en el que el test ya iba a fallar.

Historia: un ``assertTrue(event.wait(1))`` fallaba ~1 de cada 13 pasadas
completas del suite, nunca aislado, y acusaba al producto de algo que no pasaba.
"""
from __future__ import annotations

import threading
import time
import unittest
from typing import Callable

#: Plazo por defecto para esperar a un hilo, un job o un evento.
ASYNC_TIMEOUT = 10.0

#: Para los ciclos completos de pipeline, que de por si tardan segundos.
SLOW_JOB_TIMEOUT = 60.0

#: Intervalo de sondeo. Fino a proposito: fija la latencia, no el plazo.
POLL_INTERVAL = .03


def wait_until(
    predicate: Callable[[], bool],
    timeout: float = ASYNC_TIMEOUT,
    interval: float = POLL_INTERVAL,
) -> bool:
    """Sondea hasta que ``predicate`` se cumpla. Devuelve si llego a cumplirse.

    No afirma nada: quien llama se queda con sus propias comprobaciones, que son
    las que describen que se esperaba.
    """
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def assert_event(
    test: unittest.TestCase,
    event: threading.Event,
    description: str,
    timeout: float = ASYNC_TIMEOUT,
) -> None:
    """Espera un ``Event`` y, si no llega, dice que se agoto el plazo.

    Un ``assertTrue(event.wait(1))`` fallaba con un escueto "False is not true",
    que no distingue un cuelgue real de una maquina lenta.
    """
    if not event.wait(timeout):
        test.fail(f"Plazo de {timeout:g}s agotado esperando: {description}")


def assert_until(
    test: unittest.TestCase,
    predicate: Callable[[], bool],
    description: str,
    timeout: float = ASYNC_TIMEOUT,
    interval: float = POLL_INTERVAL,
) -> None:
    """Sondea hasta que ``predicate`` se cumpla y falla con el motivo si no."""
    if not wait_until(predicate, timeout, interval):
        test.fail(f"Plazo de {timeout:g}s agotado esperando: {description}")
