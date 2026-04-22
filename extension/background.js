const HOST_NAME = "com.echocore.repo_bridge";

let nativePort = null;
let nextId = 1;
const pending = new Map();

function ensureNativePort() {
  if (nativePort) return nativePort;

  nativePort = browser.runtime.connectNative(HOST_NAME);

  nativePort.onMessage.addListener((msg) => {
    const id = msg && msg.id;
    if (!id || !pending.has(id)) return;
    const { resolve } = pending.get(id);
    pending.delete(id);
    resolve(msg);
  });

  nativePort.onDisconnect.addListener(() => {
    const err =
      (browser.runtime.lastError && browser.runtime.lastError.message) ||
      "Native host disconnected";
    for (const [, handlers] of pending.entries()) {
      handlers.reject(new Error(err));
    }
    pending.clear();
    nativePort = null;
  });

  return nativePort;
}

function callHost(payload) {
  return new Promise((resolve, reject) => {
    const port = ensureNativePort();
    const id = `msg-${Date.now()}-${nextId++}`;
    pending.set(id, { resolve, reject });
    try {
      port.postMessage({ id, ...payload });
    } catch (err) {
      pending.delete(id);
      reject(err);
    }
  });
}

browser.runtime.onMessage.addListener((msg) => {
  if (!msg || msg.type !== "host_call") return;
  return callHost(msg.payload);
});
