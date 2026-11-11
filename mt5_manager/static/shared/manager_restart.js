// El botón «Reiniciar manager», para las pantallas que no lo traen escrito.
//
// La portada, Portafolios activos y Generación lo montan en su propio HTML y
// lo manejan desde su script; el resto de pantallas no lo tenía, y reiniciar
// obligaba a volver a una de esas tres. Aquí se añade donde falte, con el
// mismo aviso, la misma vigilancia y el mismo texto.
//
// Se carga **antes** que `live_audit_schedule.js` y que
// `live_account_schedule.js`: los dos se colocan delante de `#restart-manager`
// si existe, así que montarlo después los dejaría en mitad de la fila.
(() => {
  const actions = document.querySelector('.header-actions');
  if (!actions || document.querySelector('#restart-manager')) return;

  const button = document.createElement('button');
  button.type = 'button';
  button.id = 'restart-manager';
  button.className = 'danger';
  button.title = 'Sincronizar Git y reconstruir el contenedor manager';
  button.textContent = 'Reiniciar manager';
  actions.appendChild(button);

  let restarting = false;

  function announce(message, error = false) {
    const element = document.querySelector('#toast');
    if (!element) {
      if (error) console.error(message);
      return;
    }
    element.textContent = message;
    element.className = error ? 'show error' : 'show';
    setTimeout(() => { element.className = ''; }, 4000);
  }

  async function body(response) {
    const text = await response.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; }
    catch (_error) { throw new Error(`Respuesta no válida (HTTP ${response.status})`); }
    if (!response.ok) throw new Error(data.error || response.statusText);
    return data;
  }

  const state = () => fetch('/api/manager/restart?lines=80', {cache: 'no-store'}).then(body);

  function idle(message, error = false) {
    restarting = false;
    button.disabled = false;
    button.textContent = 'Reiniciar manager';
    if (message) announce(message, error);
  }

  async function monitor() {
    for (let attempt = 0; restarting && attempt < 180; attempt += 1) {
      await new Promise(resolve => setTimeout(resolve, 2000));
      let current;
      // El contenedor corta la conexión al reemplazarse: eso no es un fallo.
      try { current = await state(); } catch (_error) { continue; }
      if (current.status === 'authentication_required') { location.href = '/generation.html'; return; }
      if (current.status === 'completed') { location.reload(); return; }
      if (current.status === 'failed') {
        idle(current.error || 'Falló el reinicio del manager', true);
        return;
      }
    }
    if (restarting) idle('El reinicio no terminó dentro del tiempo esperado', true);
  }

  button.addEventListener('click', async () => {
    if (!window.confirm('¿Reiniciar el manager?\n\nSe sincronizará Git y se reconstruirá el contenedor.')) return;
    button.disabled = true;
    button.textContent = 'Preparando…';
    try {
      await body(await fetch('/api/manager/restart', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}',
      }));
      button.textContent = 'Reinicio solicitado';
      announce('Sincronización y reinicio del manager solicitados');
      restarting = true;
      monitor();
    } catch (error) {
      idle(error.message, true);
    }
  });

  // Un reinicio lanzado desde otra pantalla sigue en marcha aquí: el botón
  // tiene que nacer ya bloqueado en vez de ofrecer lanzar un segundo.
  state().then(current => {
    if (['starting', 'running', 'authentication_required', 'restarting'].includes(current.status)) {
      restarting = true;
      button.disabled = true;
      button.textContent = 'Reiniciando…';
      monitor();
    }
  }).catch(() => {});
})();
