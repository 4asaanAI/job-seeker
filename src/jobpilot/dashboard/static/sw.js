// Installable on the phone's home screen. Nothing is cached: job data must always be live, and nothing
// private is stored on the device.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
