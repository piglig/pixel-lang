const {waitFor} = require('./pending-requests');

// Diagnostics precede automatic outlines; explicit builds supersede editor work.
class EditorWork {
  constructor() { this.controllers = new Set(); this.check = undefined; }
  beginCheck() {
    this.cancel();
    const controller = new AbortController();
    let resolve;
    const promise = new Promise(done => { resolve = done; });
    const ticket = {controller, promise, resolve};
    this.check = ticket;
    return ticket;
  }
  finishCheck(ticket) {
    ticket.resolve();
    if (this.check === ticket) this.check = undefined;
  }
  async beforeOutline(signal) {
    while (this.check) await waitFor(this.check.promise, signal);
  }
  track(controller) {
    this.controllers.add(controller);
    return () => this.controllers.delete(controller);
  }
  cancel() {
    for (const controller of this.controllers) controller.abort();
    this.controllers.clear();
    if (this.check) {
      this.check.controller.abort();
      this.finishCheck(this.check);
    }
  }
}
module.exports = {EditorWork};
