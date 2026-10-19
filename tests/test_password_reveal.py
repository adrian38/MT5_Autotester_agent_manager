"""El ojo que enseña la contraseña del terminal: la que se escribe y la guardada.

No hay runtime de JavaScript en este equipo, así que lo que se exige es que el
módulo exista, se sirva, lo carguen las dos pantallas que piden una cuenta de
terminal, y que no se quede nadie fuera: cualquier `input[type=password]`
nuevo lo hereda sin tocar esta pieza.

Que el manager devuelva de verdad la contraseña guardada lo comprueba
`test_live_audit_password_route`; aquí sólo se exige que la pantalla sepa
pedirla y no se quede con lo prestado.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from mt5_manager.manager_handler import STATIC_FILES

STATIC = Path(__file__).parents[1] / "mt5_manager" / "static"
PAGES_WITH_PASSWORDS = ("index.html", "live_audit.html")


class PasswordRevealTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = (STATIC / "password_reveal.js").read_text(encoding="utf-8")
        cls.styles = (STATIC / "styles.css").read_text(encoding="utf-8")
        cls.audit = (STATIC / "live_audit.js").read_text(encoding="utf-8")
        cls.manager = (STATIC / "app.js").read_text(encoding="utf-8")

    def test_the_module_is_served_and_the_two_pages_load_it(self) -> None:
        self.assertIn("password_reveal.js", STATIC_FILES)
        for name in PAGES_WITH_PASSWORDS:
            page = (STATIC / name).read_text(encoding="utf-8")
            self.assertIn('<script src="/password_reveal.js"></script>', page)
            self.assertIn('<link rel="stylesheet" href="/styles.css">', page)

    def test_no_other_page_hides_a_password_field_without_the_eye(self) -> None:
        # Si mañana aparece una contraseña en otra pantalla, esta prueba la
        # delata en vez de dejarla a ciegas.
        for page in STATIC.glob("*.html"):
            if 'type="password"' not in page.read_text(encoding="utf-8"):
                continue
            self.assertIn(page.name, PAGES_WITH_PASSWORDS)

    def test_it_reaches_every_password_field_including_the_ones_painted_later(self) -> None:
        # Las tarjetas de uso se vuelven a construir enteras al cambiar de
        # nodo: sin el observador, las contraseñas de esas tarjetas no tendrían
        # ojo.
        self.assertIn("""querySelectorAll('input[type="password"]')""", self.script)
        self.assertIn("new MutationObserver(", self.script)
        self.assertIn("{childList: true, subtree: true}", self.script)

    def test_the_button_toggles_the_type_and_says_what_it_does(self) -> None:
        self.assertIn("'Mostrar la contraseña'", self.script)
        self.assertIn("'Ocultar la contraseña'", self.script)
        self.assertIn("const reveal = input.type === 'password';", self.script)
        self.assertIn("input.type = reveal ? 'text' : 'password';", self.script)
        # Un botón dentro de un formulario que no declara su tipo lo envía.
        self.assertIn("button.type = 'button';", self.script)

    def test_a_reused_dialog_never_opens_with_the_previous_password_in_sight(self) -> None:
        self.assertIn("""querySelectorAll('.password-field > input[type="text"]')""", self.script)
        self.assertIn(
            "window.PasswordReveal?.conceal(document.querySelector('#restore-account-dialog'));",
            self.audit,
        )
        self.assertIn(
            "window.PasswordReveal?.conceal(document.querySelector('#symbol-sync-credentials'));",
            self.manager,
        )

    def test_the_eye_is_styled_where_both_pages_can_see_it(self) -> None:
        for rule in (".password-field{", ".password-reveal{", ".password-reveal.on{"):
            self.assertIn(rule, self.styles)
        # El campo deja sitio al botón en vez de quedar tapado por él.
        self.assertRegex(self.styles, re.compile(r"\.password-field>input\{[^}]*padding-right"))

    def test_the_eye_also_asks_the_manager_for_a_password_already_saved(self) -> None:
        # Lo que se enseña sobre un campo vacío no lo tiene el navegador: hay
        # que ir a buscarlo, y sólo si el campo dice de dónde.
        self.assertIn("input.dataset.passwordSource", self.script)
        self.assertIn("await fetch(url, {cache: 'no-store'})", self.script)
        self.assertIn("if (reveal) await borrow(input); else forget(input);", self.script)

    def test_a_borrowed_password_disappears_and_is_never_sent_as_a_change(self) -> None:
        # Un campo vacío significa «conserva la guardada»; si lo que trajo el
        # ojo se quedara dentro, guardar volvería a cifrar lo mismo.
        self.assertIn("input.dataset.passwordFetched = 'on';", self.script)
        self.assertRegex(self.script, re.compile(
            r"function forget\(input\) \{[^}]*delete input\.dataset\.passwordFetched;"
            r"\s*input\.value = '';", re.S,
        ))
        self.assertIn(
            "input.addEventListener('input', () => { delete input.dataset.passwordFetched; });",
            self.script,
        )
        for role in ("source", "tester"):
            self.assertIn(f"{role}_password: typedPassword(card, '{role}'),", self.audit)
        self.assertIn("window.PasswordReveal?.typed(input)", self.audit)

    def test_the_four_saved_passwords_of_the_audit_screen_say_where_to_ask(self) -> None:
        # Las dos del uso —propias o heredadas de la cuenta elegida— y la
        # cuenta final. La de sincronizar símbolos no se guarda: no hay fuente.
        self.assertIn("live-audit-password/${encodeURIComponent(reference)}", self.audit)
        self.assertIn("${passwordSource(sourceSecret)}", self.audit)
        self.assertIn("${passwordSource(testerSecret)}", self.audit)
        self.assertIn("passwordUrl(account.id)", self.audit)
        self.assertIn("passwordUrl(`profile:${card.dataset.profileId}:${role}`)", self.audit)
        self.assertIn("passwordUrl('restore:terminal')", self.audit)
        self.assertNotIn("data-password-source", self.manager)

    def test_a_failed_request_says_so_instead_of_leaving_the_field_empty(self) -> None:
        self.assertIn("new CustomEvent('password-reveal-error'", self.script)
        self.assertIn(
            "document.addEventListener('password-reveal-error', event => toast(event.detail.message, true));",
            self.audit,
        )


if __name__ == "__main__":
    unittest.main()
