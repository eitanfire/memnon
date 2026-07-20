// Active Schema — Service Worker
// Handles the PWA Share Target for audio files shared from Voice Memos

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(clients.claim()));

self.addEventListener("fetch", event => {
  const url = new URL(event.request.url);

  if (url.pathname !== "/share-target" || event.request.method !== "POST") {
    return;
  }

  event.respondWith(handleShareTarget(event.request));
});

async function handleShareTarget(request) {
  const formData = await request.formData();
  const file = formData.get("file");

  if (file) {
    // Store the file in IndexedDB so the dashboard can pick it up
    await storeSharedFile(file);
  }

  // Redirect to dashboard — it will detect and upload the pending file
  return Response.redirect("/dashboard?shared=1", 303);
}

async function storeSharedFile(file) {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open("active-schema", 1);
    req.onupgradeneeded = e => {
      e.target.result.createObjectStore("shared-files", { autoIncrement: true });
    };
    req.onsuccess = e => {
      const db = e.target.result;
      const tx = db.transaction("shared-files", "readwrite");
      tx.objectStore("shared-files").add({ file, timestamp: Date.now() });
      tx.oncomplete = resolve;
      tx.onerror = reject;
    };
    req.onerror = reject;
  });
}
