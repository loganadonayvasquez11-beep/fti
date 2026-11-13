self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", (event) => {
  if (!event.data) return;
  let message;
  try {
    message = event.data.json();
  } catch (error) {
    return;
  }
  if (message.closeTag) {
    event.waitUntil(
      self.registration.getNotifications({ tag: message.closeTag }).then((items) => {
        if (!items.length) return;
        return self.registration.showNotification("Solicitud atendida", {
          body: "Ya no requiere atención.",
          tag: message.closeTag,
          renotify: false,
          silent: true,
        }).then(() => new Promise((resolve) => setTimeout(resolve, 1200)))
          .then(() => self.registration.getNotifications({ tag: message.closeTag }))
          .then((current) => current.forEach((item) => item.close()));
      }),
    );
    return;
  }
  if (!message.title || !message.tag) return;
  event.waitUntil(
    self.registration.showNotification(message.title, {
      body: message.body || "",
      icon: "/static/Logo.png",
      tag: message.tag,
      renotify: true,
      requireInteraction: true,
      data: { url: message.url || "/" },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const destination = new URL(event.notification.data?.url || "/", self.location.origin).href;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if (client.url.startsWith(self.location.origin) && "focus" in client) {
          client.navigate(destination);
          return client.focus();
        }
      }
      return self.clients.openWindow(destination);
    }),
  );
});
