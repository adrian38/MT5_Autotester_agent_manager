"""El ojo que enseña la contraseña del terminal mientras se escribe.

No hay runtime de JavaScript en este equipo, así que lo que se exige es que el
módulo exista, se sirva, lo carguen las dos pantallas que piden una cuenta de
terminal, y que no se quede nadie fuera: cualquier `input[type=password]`
nuevo lo hereda sin tocar esta pieza.
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
        self.assertIn("input.type = input.type === 'password' ? 'text' : 'password';", self.script)
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

    def test_it_does_not_promise_to_show_what_the_browser_never_receives(self) -> None:
        # Las guardadas viajan cifradas y el manager sólo devuelve un sí o un
        # no. El módulo lo dice, para que nadie lo lea como un fallo.
        self.assertIn("Sólo enseña lo que hay escrito en el navegador", self.script)
        self.assertNotIn("password_saved", self.script)


if __name__ == "__main__":
    unittest.main()
