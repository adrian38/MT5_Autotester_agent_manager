"""El botón superior del cron y su campana, comprobados por texto.

Salieron de `test_live_audit_cron.py` al pasar ese fichero de 600 líneas. No
comparten nada con el resto: aquí no hay servidor falso ni aritmética de
citas, sólo los ficheros servidos y lo que dicen.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class CronScreenTests(unittest.TestCase):
    """El botón superior y la campana, comprobados por texto.

    No hay runtime de JavaScript en este equipo, así que lo que se exige es
    que el módulo exista, se sirva, lo carguen las dos cabeceras y hable con
    los endpoints de verdad.
    """

    @classmethod
    def setUpClass(cls) -> None:
        from mt5_manager.manager_handler import STATIC_FILES

        cls.static_files = STATIC_FILES
        static = Path(__file__).resolve().parents[1] / "mt5_manager" / "static"
        cls.script = (static / "live_audit_schedule.js").read_text(encoding="utf-8")
        cls.dashboard = (static / "dashboard.html").read_text(encoding="utf-8")
        cls.generation = (static / "index.html").read_text(encoding="utf-8")
        cls.audit = (static / "live_audit.html").read_text(encoding="utf-8")

    def test_the_module_is_served_and_every_relevant_header_loads_it(self) -> None:
        for name in ("live_audit_schedule.js", "live_audit_schedule.css"):
            self.assertIn(name, self.static_files)
            for page in (self.dashboard, self.generation, self.audit):
                self.assertIn(f'/{name}', page)

    def test_the_button_and_the_bell_sit_in_the_top_buttons(self) -> None:
        self.assertIn("document.querySelector('.header-actions')", self.script)
        self.assertIn("Auditoría programada", self.script)
        self.assertIn("cron-bell", self.script)
        self.assertIn("cron-unread", self.script)

    def test_only_the_active_portfolios_header_shows_the_schedule_button(self) -> None:
        self.assertIn("data-live-audit-schedule", self.dashboard)
        self.assertNotIn("data-live-audit-schedule", self.generation)
        self.assertNotIn("data-live-audit-schedule", self.audit)
        self.assertIn(
            "if (showsAuditSchedule) actions.insertBefore(open, anchor || null)",
            self.script,
        )

    def test_the_dialog_edits_every_piece_the_user_asked_for(self) -> None:
        for token in (
            "cron-weekday", "cron-hour", "cron-minute", "cron-timezone",
            "cron-retry-attempts", "cron-retry-delay", "cron-only-live", "cron-chain",
            "cron-multiterminal",
            "Sólo los portafolios marcados como activos",
            "Encadenar con la última auditoría",
            "Usar el multiterminal del nodo",
            "use_multiterminal: field('#cron-multiterminal').checked,",
        ):
            self.assertIn(token, self.script)

    def test_the_dialog_exposes_overrides_and_confirms_catch_up(self) -> None:
        self.assertIn("MT5_MANAGER_LIVE_AUDIT_SCHEDULER impone", self.script)
        self.assertIn("el cron puede lanzar inmediatamente las citas vencidas", self.script)
        self.assertIn("scheduler_issues", self.script)

    def test_the_dialog_can_launch_a_persisted_gap_recovery(self) -> None:
        self.assertIn("Recuperar periodos pendientes", self.script)
        self.assertIn("cron-catch-up", self.script)
        self.assertIn("/api/live-audit-scheduler-catch-up", self.script)
        self.assertIn("de una en una por agente", self.script)

    def test_the_bar_shows_what_is_running_and_how_much_is_left(self) -> None:
        # Escondido mientras no pasa nada, y se pregunta más seguido mientras
        # algo corre, porque la fase cambia.
        self.assertIn("cron-activity", self.script)
        self.assertIn("/api/live-audit-activity", self.script)
        self.assertIn("activity.hidden = !state.active", self.script)
        self.assertIn("Recuperación en cola", self.script)
        self.assertIn("Auditando ${running.length} portafolios", self.script)
        self.assertIn("ACTIVITY_LIVE_MS", self.script)
        self.assertIn("ACTIVITY_QUIET_MS", self.script)

    def test_the_bar_sits_above_the_buttons_and_not_among_them(self) -> None:
        # Petición del usuario (2026-10-21): el sitio tiene que ser fijo. Con
        # la chapa suelta en la cabecera lo decidía el ancho del título, y
        # salía en un sitio distinto en la portada y en el detalle del
        # portafolio. Ahora la fila va envuelta en una columna y la chapa es
        # su primera línea, pegada a la derecha como los botones.
        self.assertIn("column.className = 'cron-activity-column'", self.script)
        self.assertIn("actions.parentElement.insertBefore(column, actions)", self.script)
        self.assertIn("column.append(activity, actions)", self.script)

    def test_the_bar_measures_the_whole_batch_not_only_the_current_use(self) -> None:
        self.assertIn("(queue.done + current) / queue.total", self.script)
        self.assertIn("${queue.done} de ${queue.total}", self.script)

    def test_the_activity_chip_has_its_own_style(self) -> None:
        static = Path(__file__).resolve().parents[1] / "mt5_manager" / "static"
        styles = (static / "live_audit_schedule.css").read_text(encoding="utf-8")
        for selector in (".cron-activity", ".cron-activity-dot", ".cron-activity-bar"):
            self.assertIn(selector, styles)
        # `dashboard.css` no declara `[hidden]`: sin esta regla el display:flex
        # de la barra gana y se queda visible y vacía cuando no pasa nada.
        self.assertIn(".cron-activity[hidden]{display:none}", styles)
        # La columna que la pone encima de los botones: sin su regla, el
        # envoltorio que crea el script es un div cualquiera y la chapa vuelve
        # a quedar donde caiga.
        self.assertIn(
            ".cron-activity-column{display:flex;flex-direction:column;"
            "align-items:flex-end;gap:12px}",
            styles,
        )
        # Un punto que late es ruido para quien pidió menos movimiento.
        self.assertIn("prefers-reduced-motion", styles)

    def test_the_dialog_promises_the_creation_floor_and_its_only_exception(self) -> None:
        self.assertIn("Nunca empieza antes", self.script)
        self.assertIn("día en que se creó el portafolio", self.script)
        self.assertIn("sólo una mejora o un arreglo rescata", self.script)

    def test_the_desktop_notices_are_asked_once_and_can_be_turned_on_later(self) -> None:
        # El navegador descarta la petición de permiso que no sale de un gesto
        # del usuario, así que se pregunta con un botón, una sola vez, y el de
        # la campana queda para después.
        self.assertIn("Notification.requestPermission()", self.script)
        self.assertIn("cron-push-ask", self.script)
        self.assertIn("cron-push-now", self.script)
        self.assertIn("cron-push-later", self.script)
        self.assertIn("'cron-push-asked'", self.script)
        self.assertIn("¿Quieres que el navegador te avise?", self.script)
        # El botón permanente vive en la campana.
        self.assertIn("field('#cron-push').addEventListener", self.script)

    def test_it_does_not_offer_what_the_browser_cannot_give(self) -> None:
        # `Notification` exige contexto seguro: por IP de la red local no hay
        # permiso que pedir, y un botón que no hace nada es peor que decirlo.
        self.assertIn("window.isSecureContext", self.script)
        self.assertIn("'unsupported'", self.script)
        self.assertIn("sólo los permite en localhost o por https", self.script)
        # En iOS no hay `Notification` en una pestaña normal, así que el caso
        # de instalar se decide **antes** que el de «este navegador no puede»:
        # al revés, un iPhone leería que no tiene avisos y es falso.
        self.assertIn("'needs-install'", self.script)
        self.assertIn("pantalla de inicio", self.script)
        self.assertLess(
            self.script.index("needsInstall()"), self.script.index("'unsupported'"),
        )
        # Desde otro equipo el certificado no está a mano: lo sirve el manager.
        self.assertIn('href="/ca.crt"', self.script)
        self.assertIn("cron-authority", self.script)

    def test_it_only_offers_the_authority_when_the_manager_has_one(self) -> None:
        # Detrás del proxy público el certificado lo firma Let's Encrypt, no
        # hay CA que instalar y `/ca.crt` da 404: el enlace era una descarga
        # rota y el consejo de instalarla, falso. Se pregunta antes de ofrecer.
        self.assertIn("fetch('/ca.crt'", self.script)
        self.assertIn("field('#cron-authority').hidden = !authority", self.script)

    def test_a_click_that_the_browser_swallows_still_says_something(self) -> None:
        # Chrome descarta en silencio la peticion de permiso en una pagina con
        # el certificado en rojo: la promesa vuelve en `default` sin preguntar
        # y el boton parecia roto.
        self.assertIn("Pidiendo permiso…", self.script)
        # Y si lo pide en silencio -un icono en la barra en vez de la ventana-,
        # la promesa se queda esperando y el botón parecía colgado.
        self.assertIn("lo está pidiendo en silencio", self.script)
        self.assertIn("clearTimeout(quiet)", self.script)
        self.assertIn("blockedHint", self.script)
        self.assertIn("instala runtime/tls/ca.crt", self.script)
        self.assertIn("result === 'default'", self.script)

    def test_the_first_poll_does_not_replay_the_whole_bell(self) -> None:
        self.assertIn("'cron-push-last-id'", self.script)
        self.assertIn("pushNew(notifications.events)", self.script)
        self.assertIn("event.kind !== 'audit_started'", self.script)

    def test_it_talks_to_the_real_endpoints(self) -> None:
        for endpoint in (
            "/api/live-audit-scheduler-config",
            "/api/live-audit-notifications?limit=50",
            "/api/live-audit-notifications/read",
            "/api/live-audit-notifications/clear",
        ):
            self.assertIn(endpoint, self.script)

    def test_targets_are_grouped_and_blocked_uses_can_be_fixed(self) -> None:
        self.assertIn("uso${rows.length === 1 ? '' : 's'} se ejecutará", self.script)
        self.assertIn("uso${blocked.length === 1 ? '' : 's'} no se ejecutará", self.script)
        self.assertIn("/live_audit.html?node=${encodeURIComponent(row.node_id)}", self.script)
        self.assertIn("Configurar</a>", self.script)


if __name__ == "__main__":
    unittest.main()
