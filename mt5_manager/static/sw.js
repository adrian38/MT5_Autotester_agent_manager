// El service worker de los avisos: lo único del manager que corre con el
// navegador cerrado.
//
// Se sirve desde la raíz a propósito. Un service worker sólo manda en su
// carpeta y en las de debajo, así que colgado de cualquier subruta no vería el
// resto del manager y no podría abrir la pantalla del aviso.
//
// No cachea nada. No es una web offline: su único trabajo es despertarse con
// un push, enseñar el aviso y llevar al usuario a la pantalla que lo explica.
// Cachear el manager sería peor que no hacerlo —una pantalla vieja mintiendo
// sobre portafolios que ya cambiaron— y ese no es el problema que resuelve.

// Sin esto el worker nuevo espera a que se cierren todas las pestañas: un
// arreglo del aviso no llegaría hasta el siguiente reinicio del navegador.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));

const FALLBACK = {
  title: 'Aviso del manager',
  body: 'Ábrelo en la campana del manager.',
  tag: 'manager',
  link: '/',
};

// `userVisibleOnly` obliga a enseñar un aviso en **cada** push. Si no sale
// ninguno, el navegador enseña el suyo —«este sitio se ha actualizado en
// segundo plano»— y, repetido, revoca el permiso. Por eso un payload que no
// se entiende también acaba en un aviso.
function payloadOf(data) {
  if (!data) return FALLBACK;
  try {
    const value = data.json();
    return value && typeof value === 'object' ? value : FALLBACK;
  } catch (_error) {
    return {...FALLBACK, body: data.text() || FALLBACK.body};
  }
}

self.addEventListener('push', event => {
  const payload = payloadOf(event.data);
  event.waitUntil(self.registration.showNotification(
    payload.title || FALLBACK.title,
    {
      body: payload.body || '',
      // El mismo `tag` que usa la pestaña abierta: si ésta ya enseñó el aviso
      // con la API `Notification`, el push lo reemplaza en vez de duplicarlo.
      tag: payload.tag || FALLBACK.tag,
      icon: '/icon-192.png',
      badge: '/icon-192.png',
      data: {link: payload.link || FALLBACK.link},
      // Un fallo o un bloqueo no se puede perder por no estar mirando.
      requireInteraction: payload.level === 'error' || payload.level === 'warning',
    },
  ));
});

async function openManager(link) {
  const target = new URL(link || '/', self.location.origin).href;
  const windows = await self.clients.matchAll({
    type: 'window', includeUncontrolled: true,
  });
  const exact = windows.find(client => client.url === target);
  if (exact) return exact.focus();
  // Reusar la ventana que ya está abierta en vez de abrir otra: el usuario
  // suele tener el manager puesto, y acabaría con una pestaña por aviso.
  const open = windows[0];
  if (open && typeof open.navigate === 'function') {
    await open.focus();
    return open.navigate(target);
  }
  return self.clients.openWindow(target);
}

self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(openManager((event.notification.data || {}).link));
});

function applicationServerKey(text) {
  const padded = String(text || '').replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(padded + '='.repeat((4 - padded.length % 4) % 4));
  return Uint8Array.from(raw, character => character.charCodeAt(0));
}

// El navegador rota la suscripción por su cuenta —caduca, o se renueva al
// actualizarse— y la antigua deja de servir. Si no se repone aquí, los avisos
// dejan de llegar sin que nadie toque nada y sin ningún error a la vista.
async function resubscribe() {
  const response = await fetch('/api/web-push/config', {cache: 'no-store'});
  const config = await response.json();
  if (!config || !config.public_key) return;
  const subscription = await self.registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: applicationServerKey(config.public_key),
  });
  await fetch('/api/web-push/subscribe', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({subscription}),
  });
}

self.addEventListener('pushsubscriptionchange', event => {
  event.waitUntil(resubscribe().catch(() => {}));
});
