// Share only in-flight reads; a completed filesystem snapshot is never cached.
class PendingRequests {
  constructor() { this.requests = new Map(); }
  run(key, operation) {
    if (this.requests.has(key)) return this.requests.get(key);
    const promise = Promise.resolve().then(operation).finally(() => {
      if (this.requests.get(key) === promise) this.requests.delete(key);
    });
    this.requests.set(key, promise);
    return promise;
  }
  clear() { this.requests.clear(); }
}
function waitFor(promise, signal) {
  if (!signal) return promise;
  return new Promise((resolve, reject) => {
    const cleanup = () => signal.removeEventListener('abort', abort);
    const abort = () => { cleanup(); reject(Object.assign(new Error('Compilation cancelled'), {name: 'AbortError'})); };
    signal.addEventListener('abort', abort, {once: true});
    if (signal.aborted) abort();
    // Observe the shared operation even if this consumer is already cancelled.
    promise.then(value => { cleanup(); resolve(value); }, error => { cleanup(); reject(error); });
  });
}
module.exports = {PendingRequests, waitFor};
