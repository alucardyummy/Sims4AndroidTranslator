

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
    badge: '/img/plumBD.png',
    data: { url: data.url || '/' },
    tag: data.tag || 'update',
    renotify: true,
    vibrate: [80, 40, 80]
  };

  // Se o app (WebView nativa) estiver de pé, oferece a notificação pra ela
  // primeiro — ela tem a ponte pra mostrar com a cor de marca. Só cai pro
  // padrão do navegador (sem cor) se nenhum client assumir.
  event.waitUntil((async () => {
    let handledNatively = false;
    try {
      const clientList = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
      for (const client of clientList) {
        const result = await new Promise((resolve) => {
          const channel = new MessageChannel();
          const timer = setTimeout(() => resolve(null), 300);
          channel.port1.onmessage = (e) => { clearTimeout(timer); resolve(e.data); };
          client.postMessage(
            { type: 'native-notify-check', payload: { title, body: options.body, url: options.data.url, tag: options.tag } },
            [channel.port2]
          );
        });
        if (result && result.handled) { handledNatively = true; break; }
      }
    } catch (e) { /* segue pro fallback abaixo */ }

    if (!handledNatively) {
      await self.registration.showNotification(title, options);
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
