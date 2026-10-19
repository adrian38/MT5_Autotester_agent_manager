/* El ojo de las contraseñas: mirar lo que se está escribiendo.
 *
 * Las contraseñas de los terminales son largas y las da el bróker; escribirlas
 * a ciegas y descubrir el error cuando la auditoría falla a los veinte minutos
 * no es aceptable. Esto le pone a cada `input[type=password]` un botón que la
 * enseña mientras se escribe.
 *
 * **Sólo enseña lo que hay escrito en el navegador.** Las guardadas viajan
 * cifradas y el manager nunca las devuelve: la pantalla recibe `*_password_
 * saved`, un sí o un no, así que sobre un campo vacío el ojo no tiene nada que
 * enseñar. Eso es a propósito y no lo arregla esta pieza.
 *
 * Se aplica solo, también a lo que se pinta después: las tarjetas de uso se
 * vuelven a construir enteras cada vez que cambia el nodo.
 */
(() => {
  const SHOW = 'Mostrar la contraseña';
  const HIDE = 'Ocultar la contraseña';
  const EYE = '<svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">'
    + '<path d="M1.6 10S4.9 4.6 10 4.6 18.4 10 18.4 10 15.1 15.4 10 15.4 1.6 10 1.6 10Z"/>'
    + '<circle cx="10" cy="10" r="2.7"/>'
    + '<line class="password-reveal-slash" x1="3.6" y1="16.4" x2="16.4" y2="3.6"/>'
    + '</svg>';

  function label(button, input) {
    const text = input.type === 'password' ? SHOW : HIDE;
    button.title = text;
    button.setAttribute('aria-label', text);
    button.classList.toggle('on', input.type !== 'password');
  }

  function decorate(input) {
    if (input.dataset.passwordReveal || !input.parentNode) return;
    input.dataset.passwordReveal = 'on';
    const field = document.createElement('span');
    field.className = 'password-field';
    input.parentNode.insertBefore(field, input);
    field.appendChild(input);
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'password-reveal';
    // Fuera del tabulador: entre la contraseña y el siguiente campo no se
    // espera un paso intermedio, y con teclado se escribe a ciegas igual.
    button.tabIndex = -1;
    button.innerHTML = EYE;
    button.addEventListener('click', () => {
      input.type = input.type === 'password' ? 'text' : 'password';
      label(button, input);
      input.focus();
    });
    label(button, input);
    field.appendChild(button);
  }

  function apply(root) {
    (root || document).querySelectorAll('input[type="password"]').forEach(decorate);
  }

  // Al cerrar o reutilizar un diálogo la contraseña se borra; dejarla
  // «visible» para la siguiente sería enseñar la de otra cuenta.
  function conceal(root) {
    (root || document)
      .querySelectorAll('.password-field > input[type="text"]')
      .forEach(input => {
        input.type = 'password';
        const button = input.parentNode.querySelector('.password-reveal');
        if (button) label(button, input);
      });
  }

  let scheduled = false;
  function watch() {
    if (!document.body || typeof MutationObserver !== 'function') return;
    new MutationObserver(() => {
      if (scheduled) return;
      scheduled = true;
      requestAnimationFrame(() => { scheduled = false; apply(); });
    }).observe(document.body, {childList: true, subtree: true});
  }

  function start() {
    apply();
    watch();
  }

  window.PasswordReveal = {apply, conceal};
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
