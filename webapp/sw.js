// Служебный скрипт сайта «Зооконсьерж»: показывает уведомления (Web Push), даже когда сайт закрыт.
// Содержимое уведомления приходит зашифрованным с сервера; здесь его только показываем.
self.addEventListener('install', function (e) { self.skipWaiting(); });
self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });

self.addEventListener('push', function (e) {
  var d = {};
  try { d = e.data ? e.data.json() : {}; } catch (x) { d = { body: e.data ? e.data.text() : '' }; }
  var title = d.title || 'Зооконсьерж';
  // красная точка на иконке приложения (iPhone, «Домой»): видно, что есть новое, даже если уведомление смахнули
  try { if (self.navigator && self.navigator.setAppBadge) self.navigator.setAppBadge(); } catch (x) {}
  e.waitUntil(self.registration.showNotification(title, {
    body: d.body || 'Новое сообщение',
    icon: '/icons/icon-192.png',
    badge: '/icons/favicon-32.png',
    tag: 'zc-chat',
    renotify: true,
    data: { url: d.url || '/chat' }
  }));
});

self.addEventListener('notificationclick', function (e) {
  e.notification.close();
  var url = (e.notification.data && e.notification.data.url) || '/chat';
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
    for (var i = 0; i < list.length; i++) {
      var c = list[i];
      if (c.url.indexOf(url) !== -1 && 'focus' in c) return c.focus();
    }
    return self.clients.openWindow(url);
  }));
});
