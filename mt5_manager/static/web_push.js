// El lado del navegador de los avisos: registrar el service worker,
// suscribirse y enseñar el aviso por donde cada navegador lo acepta.
//
// Está aparte de `live_audit_schedule.js` porque no es la campana: es lo que
// hace que el aviso llegue **sin** campana abierta, y lo cargan las mismas
// pantallas sólo porque son las que montan el botón.
//
// Dos cosas que no se deducen del código y explican los síntomas que trajeron
// todo esto:
//
// - **`new Notification(...)` no existe en Android.** Chrome para Android
//   responde `Illegal constructor` y exige `registration.showNotification()`.
//   El permiso se concedía, no salía ningún aviso y la excepción moría en el
//   `catch` del sondeo: silencio absoluto y ningún error visible.
// - **En iOS no hay avisos en una pestaña.** Safari sólo da `Notification` y
//   `PushManager` cuando la web está instalada en la pantalla de inicio
//   (iOS 16.4+). De ahí el manifiesto y el estado `needs-install`: sin eso,
//   el botón decía «este navegador no tiene avisos» y era mentira.
(() => {
  const WORKER = '/sw.js';
  let registration = null;
  let config = null;

  const supported = () => (
    'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window
  );

  // `navigator.platform` es «MacIntel» también en el iPad desde iPadOS 13, y
  // lo que lo distingue de un Mac es que tiene puntos táctiles.
  const apple = () => (
    /iPad|iPhone|iPod/.test(navigator.userAgent)
    || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)
  );

  const installed = () => (
    window.matchMedia('(display-mode: standalone)').matches
    || window.navigator.standalone === true
  );

  function keyBytes(text) {
    const padded = String(text || '').replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(padded + '='.repeat((4 - padded.length % 4) % 4));
    return Uint8Array.from(raw, character => character.charCodeAt(0));
  }

  function sameKey(subscription, publicKey) {
    const current = (subscription.options || {}).applicationServerKey;
    if (!current) return false;
    const mine = keyBytes(publicKey);
    const theirs = new Uint8Array(current);
    return mine.length === theirs.length
      && mine.every((value, index) => value === theirs[index]);
  }

  async function register() {
    if (!supported() || !window.isSecureContext) return null;
    if (!registration) {
      registration = await navigator.serviceWorker.register(WORKER, {scope: '/'});
      // `register()` vuelve antes de que el worker esté al mando, y
      // `pushManager` necesita uno activo.
      await navigator.serviceWorker.ready;
    }
    return registration;
  }

  async function load(force) {
    if (config && !force) return config;
    const response = await fetch('/api/web-push/config', {cache: 'no-store'});
    config = await response.json();
    if (!response.ok) throw new Error(config.error || response.statusText);
    return config;
  }

  // Siempre se manda la suscripción al manager, incluso cuando el navegador ya
  // la tenía: es lo que repone la lista si `runtime/` se perdió o si el
  // dispositivo se dio de baja desde otro sitio. Sin esto, un navegador con
  // permiso concedido quedaría convencido de estar suscrito a un manager que
  // no sabe de él.
  async function activate() {
    const worker = await register();
    if (!worker) throw new Error('Este navegador no puede recibir avisos aquí.');
    const data = await load(true);
    if (!data.public_key) {
      throw new Error(data.error || 'El manager no tiene clave de avisos.');
    }
    let subscription = await worker.pushManager.getSubscription();
    if (subscription && !sameKey(subscription, data.public_key)) {
      // La clave del manager cambió: la suscripción vieja está firmada para
      // otra y el servicio de push rechazaría el aviso con un 403.
      await subscription.unsubscribe();
      subscription = null;
    }
    if (!subscription) {
      subscription = await worker.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: keyBytes(data.public_key),
      });
    }
    const response = await fetch('/api/web-push/subscribe', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({subscription}),
    });
    config = await response.json();
    if (!response.ok) throw new Error(config.error || response.statusText);
    return config;
  }

  // El aviso de la pestaña abierta. Pasa por el service worker donde hay uno
  // -que es donde el constructor no existe- y cae al constructor sólo en un
  // escritorio sin worker registrado todavía.
  async function show(title, options) {
    if (registration) return registration.showNotification(title, options);
    const worker = 'serviceWorker' in navigator
      ? await navigator.serviceWorker.getRegistration('/') : null;
    if (worker) return worker.showNotification(title, options);
    return new Notification(title, options);
  }

  async function test() {
    const response = await fetch('/api/web-push/test', {method: 'POST'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || response.statusText);
    config = data;
    return data;
  }

  window.ManagerPush = {
    supported, apple, installed, register, load, activate, show, test,
    // Para que la interfaz pueda distinguir «este navegador no puede» de
    // «este navegador podría si lo instalas».
    needsInstall: () => apple() && !installed() && !supported(),
    devices: () => (config && config.total) || 0,
  };

  // Registrar el worker al cargar no pide ningún permiso ni enseña nada: es
  // lo que permite que un dispositivo ya autorizado siga recibiendo avisos
  // después de reiniciar el navegador, sin tener que volver a pulsar nada.
  if (supported() && window.isSecureContext && Notification.permission === 'granted') {
    register()
      .then(() => activate())
      .catch(error => console.warn('[web-push] no se pudo reponer la suscripción', error));
  }
})();
