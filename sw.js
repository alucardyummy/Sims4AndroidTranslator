// Service Worker do Sims 4 Translator — cuida das notificações push.
// Não faz cache de nada de propósito: o site já é leve e assim evita
// o clássico problema de PWA mostrando versão antiga em cache.

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

  const title = data.title || 'Sims 4 Translator';
  const options = {
    body: data.body || 'Tem novidade por aqui!',
    icon: '/img/favicon.png',
    badge: '/img/favicon.png',
    data: { url: data.url || '/' },
    tag: data.tag || 'update',
    renotify: true,
    vibrate: [80, 40, 80]
  };

  event.waitUntil(self.registration.showNotification(title, options));
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
