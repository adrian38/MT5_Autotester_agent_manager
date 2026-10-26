# Los avisos llegan a cada dispositivo, no sólo al que tiene la pestaña abierta

Petición del usuario (2026-10-20): «las notificaciones sólo salen en una PC, en
otra PC no y en el móvil tampoco».

No era una causa, eran tres. Las tres estaban en el diseño anterior
(`live_audit_cron_and_bell.md`, sección «Avisos del escritorio»), que usaba la
API `Notification` del navegador sin service worker:

| Síntoma | Causa |
| --- | --- |
| La PC que sí avisa | Abre el manager en `http://localhost`, que es contexto seguro por excepción, y tiene el permiso concedido. El permiso es **por origen y por perfil de navegador**: no viaja entre equipos. |
| La otra PC, nada | Abierta por IP, `window.isSecureContext` es `false` y el navegador no ofrece permiso. O está por https y nadie pulsó «Activar» ahí: si alguna vez se pulsó «Ahora no», `cron-push-asked` impide que la tarjeta vuelva a salir y sólo queda el botón de la campana. |
| El móvil, nunca | Dos razones distintas, y ninguna se arregla configurando. |

## Las dos razones del móvil

- **Android no tiene el constructor.** Chrome para Android responde
  `Illegal constructor` a `new Notification(...)` desde hace años y exige
  `ServiceWorkerRegistration.showNotification()`. El permiso se concedía, el
  aviso no salía y la excepción moría en el `.catch(() => {})` del sondeo de la
  campana: silencio absoluto y ningún error a la vista.
- **iOS no da avisos en una pestaña.** Safari sólo expone `Notification` y
  `PushManager` cuando la web está **instalada en la pantalla de inicio**
  (iOS 16.4+). Sin manifiesto no hay nada que instalar, y el botón decía «este
  navegador no tiene avisos», que es falso.

Y aunque se arreglara el constructor, seguiría sin servir: el navegador del
móvil **congela los temporizadores de una pestaña en segundo plano**, así que
el sondeo de 60 s no corre. Un cálculo de portafolio dura horas; el aviso que
importa es justo el que nadie está mirando. Por eso esto es Web Push de verdad
y no un parche al aviso de la pestaña.

## Reparto

| Módulo | Qué hace |
| --- | --- |
| `web_push_crypto.py` | El sobre `aes128gcm` de RFC 8291. |
| `web_push_vapid.py` | La identidad del manager: clave ES256 persistida y el JWT de RFC 8292. |
| `web_push_store.py` | Los dispositivos suscritos, en `runtime/web_push_subscriptions.json`. |
| `web_push.py` | El envío, el enganche a la campana y la política de fallos. |
| `manager_push_routes.py` | `/api/web-push/config`, `/subscribe`, `/unsubscribe`, `/test`. |
| `static/sw.js` | El service worker: enseña el aviso y abre su pantalla. |
| `static/web_push.js` | Registro, suscripción y el `show()` que Android acepta. |
| `static/manifest.webmanifest` + `icon-*.png` | Lo que iOS exige para instalarlo. |
| `tools/make_push_icons.py` | Genera los iconos; no hay dependencia de imagen en el workspace. |

## Decisiones que no se deducen del código

- **El icono instalado cuenta lo mismo que la campana.** La página aplica
  `setAppBadge` al cargar, sondear, marcar como leído o vaciar. Cuando está
  cerrada, cada Web Push lleva el total no leído que había justo después de
  guardar el aviso y el service worker actualiza el badge. Las dos llamadas son
  opcionales y silenciosas porque la API no existe o no está habilitada en
  todos los navegadores; eso nunca debe impedir ni el diario ni el aviso.

- **El aviso viaja cifrado dentro del push, no se va a buscar después.** La
  alternativa era un push vacío y que el service worker pidiera el aviso al
  manager, que ahorra todo el cifrado. Falla justo cuando más importa: sin red,
  o con el móvil en una red desde la que el manager no se alcanza. Con el texto
  dentro, el aviso sale igual.
- **El cifrado sólo se puede verificar con el vector del RFC.** Una derivación
  mal hecha no lanza ninguna excepción: produce un cuerpo que el dispositivo
  descarta en silencio, y el síntoma es «el móvil no avisa», que es exactamente
  lo que no se puede depurar. `tests/manager/test_web_push_crypto.py` reproduce el
  ejemplo de la sección 5 de RFC 8291 octeto a octeto, y `tests/manager/test_web_push.py`
  monta el dispositivo del otro lado y **descifra** lo que salió por la red.
- **La clave VAPID se conserva y no se regenera sola.** El navegador la guardó
  al suscribirse y el servicio de push rechaza con 403 un aviso firmado por
  otra. Un fichero de clave ilegible se denuncia, no se sobrescribe: regenerarla
  dejaría sin avisos a todos los dispositivos a la vez y nadie sabría por qué.
  Se crea perezosamente, al primer uso, para no escribir en `runtime/` en un
  equipo que nunca va a usar avisos.
- **El `subject` sale del correo que ya hay en `.env`.** Apple contesta 400 a un
  JWT cuyo `sub` no sea un `mailto:` o una URL de verdad. `docker-compose.yml`
  le pasa al manager el `ACME_EMAIL` y el `MANAGER_DOMAIN` del proxy.
- **El enganche está en la campana, no en cada sitio que avisa.**
  `LiveAuditNotificationStore.announce` es opcional y lo pone `manager_server`.
  Así un aviso nuevo —de una auditoría, de un cálculo o de lo que venga— sale
  en el dispositivo sin tocar nada más, y el diario sigue funcionando sin clave
  y sin avisos.
- **Nada de esto puede romper a quien anota el aviso.** `add()` se llama dentro
  del `try` del trabajador que calcula el portafolio: una excepción al entregar
  marcaría como **fallido** un cálculo que terminó bien. La entrega va en su
  propio hilo y hay dos redes de seguridad, la de `_announce` y la del hilo. Es
  la misma razón que la de `_announce_job` en `portfolio_job_bell_notices.md`.
- **El mismo `tag` en los dos caminos.** La pestaña abierta sigue enseñando su
  aviso —llega antes, no pasa por el servicio del navegador— y el push llega
  después: con `tag: cron-<id>` el segundo reemplaza al primero en vez de
  duplicarlo.
- **404 y 410 no son un fallo de red.** Son una suscripción que ya no existe:
  se borra en el primer intento. Lo demás se anota y, tras cinco fallos
  seguidos, ese dispositivo deja de intentarse para que un aviso no se pase el
  tiempo de espera de red por cada móvil que ya no está.
- **«Probar aviso» no es un lujo.** Es el único punto del camino que el usuario
  puede comprobar: entre pulsar «Activar» y el aviso de un cálculo de horas hay
  una clave, un JWT, un cifrado y un servicio de push ajeno. Sin esa ruta, un
  fallo se descubre de madrugada y sin ningún mensaje. Entrega **en síncrono** y
  devuelve lo que contestó cada dispositivo.
- **El service worker no cachea nada.** No es una web offline: su único trabajo
  es despertarse con un push. Cachear el manager sería peor que no hacerlo —una
  pantalla vieja mintiendo sobre portafolios que ya cambiaron—.

## Lo que el usuario tiene que hacer una vez por dispositivo

Los avisos son por origen y por navegador, así que no hay forma de activarlos
en los tres sitios desde uno:

1. Abrir el manager **por su dirección https** (la del proxy, sin puerto). Por
   IP y en `http` no hay permiso que pedir, y el botón lo dice.
2. Campana → «Activar avisos en este dispositivo» → permitir.
3. «Probar aviso» para comprobarlo en el momento.
4. En iPhone o iPad, antes de todo eso: Compartir → «Añadir a pantalla de
   inicio», y abrir el manager desde ese icono. En una pestaña de Safari no hay
   avisos, los instale quien los instale.
