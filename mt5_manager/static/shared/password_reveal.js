/* El ojo de las contraseñas: mirar lo que se está escribiendo.
 *
 * Las contraseñas de los terminales son largas y las da el bróker; escribirlas
 * a ciegas y descubrir el error cuando la auditoría falla a los veinte minutos
 * no es aceptable. Esto le pone a cada `input[type=password]` un botón que la
 * enseña mientras se escribe.
 *
 * **También enseña una ya guardada.** Un campo vacío que lleve
 * `data-password-source` tiene detrás la ruta del manager que la descifra: el
 * ojo la pide y la pinta. Lo que llega así se marca como prestado, de manera
 * que al taparlo vuelve a desaparecer y el formulario no lo envía como si el
 * usuario hubiera escrito una contraseña nueva; en cuanto se teclea encima
 * deja de ser prestado y pasa a ser suyo.
 *
 * Sin esa fuente el ojo sigue enseñando sólo lo que hay escrito. Si la
 * petición falla, el campo se queda vacío y el input emite
 * `password-reveal-error`, que la pantalla convierte en aviso.
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

  // Prestada: vino del manager, no la escribió nadie. Se borra al taparla
  // para que el campo vacío siga significando «conserva la guardada».
  function forget(input) {
    if (!input.dataset.passwordFetched) return;
    delete input.dataset.passwordFetched;
    input.value = '';
  }

  function typed(input) {
    return input && input.dataset.passwordFetched ? '' : (input ? input.value : '');
  }

  async function borrow(input) {
    const url = input.dataset.passwordSource || '';
    if (!url || input.value) return;
    try {
      const response = await fetch(url, {cache: 'no-store'});
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.password) throw new Error(data.error || response.statusText);
      input.value = data.password;
      input.dataset.passwordFetched = 'on';
    } catch (error) {
      input.dispatchEvent(new CustomEvent('password-reveal-error', {
        bubbles: true,
        detail: {message: `No se pudo recuperar la contraseña guardada: ${error.message}`},
      }));
    }
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
    button.addEventListener('click', async () => {
      const reveal = input.type === 'password';
      if (reveal) await borrow(input); else forget(input);
      input.type = reveal ? 'text' : 'password';
      label(button, input);
      input.focus();
    });
    input.addEventListener('input', () => { delete input.dataset.passwordFetched; });
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
        forget(input);
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

  window.PasswordReveal = {apply, conceal, typed};
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
