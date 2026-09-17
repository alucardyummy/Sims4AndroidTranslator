self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('push', (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { title: 'Sims 4 Translator', body: event.data ? event.data.text() : '' };
  }

  if (data.type === 'dismiss-updates') {
    event.waitUntil((async () => {
      // ids específicos (novo formato) — cada notificação tem sua própria tag
      const ids = Array.isArray(data.ids) ? data.ids : [];
      const tags = ids.length ? ids.map((id) => `update-${id}`) : ['update'];

      for (const tag of tags) {
        const notifs = await self.registration.getNotifications({ tag });
        notifs.forEach((n) => n.close());
      }

      const clientList = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
      for (const client of clientList) {
        client.postMessage({ type: 'updates-changed' });
      }
    })());
    return;
  }

  if (data.type === 'edit-update') {
    event.waitUntil((async () => {
      const tag = data.tag || `update-${data.id}`;
      const notifs = await self.registration.getNotifications({ tag });

      // só atualiza o texto se ela ainda estiver na barra — se a pessoa já
      // dispensou, não faz nada (não ressuscita notificação já fechada)
      if (notifs.length) {
        await self.registration.showNotification(data.title, {
          body: data.body,
          icon: '/img/notif-empty.png',
          badge: '/img/plumBD.png',
          data: { url: data.url || '/' },
          tag,
          renotify: false,
          silent: true
        });
      }

      const clientList = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
      for (const client of clientList) {
        client.postMessage({ type: 'updates-changed' });
      }
    })());
    return;
  }

  const title = data.title || 'Sims 4 Translator';
  const options = {
    body: data.body || 'Tem novidade por aqui!',
    icon: '/img/notif-empty.png',
    badge: '/img/plumBD.png',
    data: { url: data.url || '/' },
    tag: data.tag || 'update',
    renotify: true,
    vibrate: [80, 40, 80]
  };

  // A cor de marca é aplicada do lado nativo pelo BrandedNotificationDelegationService
  // (no app TWA), que intercepta a notificação delegada pelo Chrome antes de
  // exibi-la — não depende de nenhuma aba aberta pra funcionar.
  event.waitUntil((async () => {
    await self.registration.showNotification(title, options);

    // Avisa qualquer aba aberta do site que chegou novidade, pra lista da
    // página se atualizar sozinha, sem precisar recarregar.
    const clientList = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const client of clientList) {
      client.postMessage({ type: 'new-update' });
    }
  })());
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) || '/';

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if (client.url.includes(self.location.origin) && 'focus' in client) {
          client.navigate(targetUrl);
          return client.focus();
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(targetUrl);
      }
    })
  );
});
