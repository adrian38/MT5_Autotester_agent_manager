// La navegación común: los mismos siete destinos, en el mismo orden, en todas
// las pantallas.
//
// Hasta el 2026-11-13 cada HTML escribía sus enlaces a mano, así que el orden
// del desplegable «Menú» era el orden en que a cada pantalla le habían ido
// cayendo: la portada ofrecía cuatro, «Portafolios activos» otros cuatro
// distintos, Generación otros cinco y las catorce restantes ninguno. Un menú
// cuyo contenido y cuyo orden dependen de la pantalla no es un menú: hay que
// leerlo entero cada vez. Petición del usuario (2026-11-13): estos siete,
// siempre, en este orden, y detrás los botones propios de cada pantalla.
//
// «Cuentas en real» es el segundo y también es fijo. En el primer reparto
// se quedó como botón propio de las tres pantallas que lo escribían, y así
// volvía a caer detrás del bloque, en un sitio distinto según la pantalla:
// justo lo que se pidió evitar (corrección del usuario, 2026-11-13).
//
// Doce pantallas escribían además «Volver al generador», que era su salida
// a la raíz cuando no había bloque común. Con «Generación» en la fila de
// todas, ese botón era el mismo destino dos veces: se quitó (2026-11-13).
//
// El bloque se monta **al principio** de `.header-actions`, no delante del
// ancla como la campana: va antes de lo que cada pantalla trae escrito.
// «Actualizar» sigue abriendo la fila porque lo recoloca
// `live_audit_schedule.js`, que corre después.
(() => {
  const actions = document.querySelector('.header-actions');
  if (!actions) return;

  // El orden pedido. `url` vacía es un destino que todavía no existe: ocupa su
  // sitio apagado en vez de prometer un 404.
  const DESTINATIONS = [
    {
      label: 'Resumen del mes', url: '/monthly_summary.html', blank: true,
      className: 'cron-link summary-link', id: 'open-monthly-summary',
      title: 'El mes cerrado contra lo que ese mes prometía el backtest',
    },
    {label: 'Cuentas en real', url: '/', className: 'dashboard-link'},
    {label: 'Portafolios activos', url: '/live_portfolios.html', className: 'dashboard-link'},
    {label: 'Generación', url: '/generation.html', className: 'generation-link'},
    {
      label: 'Experimenta 1M', url: '/experiment.html', blank: true,
      className: 'experiment-link',
      title: 'Laboratorio: combinar los tres brokers en una cuenta y medir el '
        + 'camino hasta un millón en un año',
    },
    {
      label: 'Experimenta Prop', url: '/prop_experiment.html', blank: true,
      className: 'experiment-link',
      title: 'Laboratorio de carteras con candidatas y seeds para aprobar pruebas prop',
    },
    {
      label: 'Experimenta seeds', url: '', className: 'experiment-link',
      title: 'Todavía no hay pantalla: el sitio queda reservado para cuando la haya',
    },
  ];

  // La pantalla que se está viendo, con su URL, no con su fichero: tres de
  // ellas no se llaman como su ruta y quien lo decide es `manager_handler`.
  // `/index.html` es la portada por ese mismo reparto.
  const here = window.location.pathname === '/index.html' ? '/' : window.location.pathname;

  const block = document.createDocumentFragment();
  let summary = null;
  for (const item of DESTINATIONS) {
    const current = Boolean(item.url) && item.url === here;
    // El destino que aún no existe y el de la pantalla actual se pintan igual:
    // con su hueco ocupado y sin enlace. Que el sitio de cada botón no se mueva
    // de una pantalla a otra es justo lo que se pidió, y un enlace a la página
    // en la que ya estás no es navegación.
    const node = document.createElement(item.url && !current ? 'a' : 'span');
    node.className = `button ${item.className}`;
    node.textContent = item.label;
    if (item.id) node.id = item.id;
    if (item.title) node.title = item.title;
    if (item.url && !current) {
      node.href = item.url;
      // Los laboratorios y el resumen se abren aparte: se miran con la pantalla
      // de la que vienes delante.
      if (item.blank) {
        node.target = '_blank';
        node.rel = 'noopener';
      }
    } else {
      node.classList.add('nav-inert');
      if (current) node.setAttribute('aria-current', 'page');
      else node.classList.add('nav-pending');
    }
    if (item.id === 'open-monthly-summary') summary = node;
    block.appendChild(node);
  }
  actions.insertBefore(block, actions.firstElementChild);

  // La chapa del resumen, que antes montaba `monthly_summary_button.js` en las
  // dos pantallas de operación real. Ahora el botón está en las dieciocho, así
  // que la cuenta de meses sin leer también: el día 1 se ve desde donde estés.
  // Que falle no puede dejar la cabecera a medias, así que se calla.
  if (!summary) return;
  const badge = document.createElement('span');
  badge.className = 'summary-badge';
  badge.id = 'monthly-summary-badge';
  badge.hidden = true;
  summary.appendChild(badge);

  fetch('/api/monthly-summary', {cache: 'no-store'})
    .then(response => response.json())
    .then(answer => {
      const pending = (answer.unread || []).length;
      badge.hidden = !pending;
      badge.textContent = String(pending);
      summary.classList.toggle('has-unread', Boolean(pending));
    })
    .catch(() => {});
})();
