// El botón «Resumen del mes» de la barra superior, y nada más.
//
// Lo montan las dos pantallas de operación real —la portada de cuentas y
// «Portafolios activos»—, que es donde se decide si una cartera sigue; en
// Generación o Universo sería ruido. Abre `monthly_summary.html` en una
// ventana nueva (petición del usuario, 2026-10-25): el resumen se lee con las
// tarjetas de las cuentas delante, y un diálogo encima obligaba a cerrarlo
// para mirar cualquier otra cosa.
//
// Va aparte de `monthly_summary.js` porque son dos trabajos distintos: aquél
// pinta la pantalla entera y éste sólo pone un enlace con su chapa. Juntarlos
// obligaría a la portada a cargar el pintado que nunca usa.
(() => {
  const actions = document.querySelector('.header-actions');
  if (!actions) return;

  const PAGE = '/monthly_summary.html';

  const open = document.createElement('a');
  open.className = 'button cron-link summary-link';
  open.id = 'open-monthly-summary';
  open.href = PAGE;
  open.target = '_blank';
  open.rel = 'noopener';
  open.title = 'El mes cerrado contra lo que ese mes prometía el backtest';
  open.textContent = 'Resumen del mes';

  const badge = document.createElement('span');
  badge.className = 'summary-badge';
  badge.id = 'monthly-summary-badge';
  badge.hidden = true;
  open.appendChild(badge);

  // El mismo ancla que los otros dos botones de la barra, preguntado en el
  // mismo orden: «Reiniciar manager» cierra la fila y, donde no lo hay, el
  // enlace de vuelta. Lo vigila `test_header_actions_order`.
  const anchor = actions.querySelector('#restart-manager')
    || actions.querySelector('a[href="/"]');
  actions.insertBefore(open, anchor || null);

  // La chapa se pone sin abrir nada: el día 1 el botón ya dice que hay algo
  // que mirar aunque el usuario entre a otra cosa. Que falle no puede dejar la
  // pantalla a medias, así que se calla.
  fetch('/api/monthly-summary', {cache: 'no-store'})
    .then(response => response.json())
    .then(answer => {
      const pending = (answer.unread || []).length;
      badge.hidden = !pending;
      badge.textContent = String(pending);
      open.classList.toggle('has-unread', Boolean(pending));
    })
    .catch(() => {});
})();
