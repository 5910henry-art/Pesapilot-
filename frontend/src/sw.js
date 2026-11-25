import {
  cleanupOutdatedCaches,
  createHandlerBoundToURL,
  precacheAndRoute,
} from "workbox-precaching";
import { NavigationRoute, registerRoute } from "workbox-routing";

precacheAndRoute(self.__WB_MANIFEST);
cleanupOutdatedCaches();

registerRoute(
  new NavigationRoute(createHandlerBoundToURL("/index.html"), {
    denylist: [/^\/api\//],
  }),
);

self.addEventListener("message", (event) => {
  if (event.data?.type === "SKIP_WAITING") {
    event.waitUntil(self.skipWaiting());
  }
});

function safeNotificationUrl(value) {
  try {
    const url = new URL(
      typeof value === "string" ? value : "/",
      self.location.origin,
    );

    if (
      url.origin !== self.location.origin ||
      url.pathname === "/api" ||
      url.pathname.startsWith("/api/")
    ) {
      return "/";
    }

    return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    return "/";
  }
}

self.addEventListener("push", (event) => {
  let payload = {};

  try {
    payload = event.data?.json() ?? {};
  } catch {
    payload = {
      body: event.data?.text() ?? "",
    };
  }

  const title =
    typeof payload.title === "string" && payload.title.trim()
      ? payload.title.trim()
      : "PesaPilot";

  const body =
    typeof payload.body === "string"
      ? payload.body
      : "You have a new notification.";

  const options = {
    body,
    icon: "/icons/pwa-192.png",
    badge: "/icons/pwa-192.png",
    tag:
      typeof payload.id === "string"
        ? `pesapilot-${payload.id}`
        : undefined,
    renotify: false,
    data: {
      url: safeNotificationUrl(payload.url),
      notificationId:
        typeof payload.id === "string" ? payload.id : null,
    },
  };

  event.waitUntil(
    self.registration.showNotification(title, options),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();

  const targetUrl = new URL(
    safeNotificationUrl(event.notification.data?.url),
    self.location.origin,
  );

  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });

      for (const client of windows) {
        if (new URL(client.url).origin === self.location.origin) {
          await client.navigate(targetUrl.href);
          return client.focus();
        }
      }

      return self.clients.openWindow(targetUrl.href);
    })(),
  );
});
