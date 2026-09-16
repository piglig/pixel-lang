const { spawn } = require("node:child_process");
const readline = require("node:readline");
function encodeWire(value) {
  if (Buffer.isBuffer(value)) return { $bytes: value.toString("base64") };
  if (Array.isArray(value)) return value.map(encodeWire);
  if (value && typeof value === "object") {
    const entries = Object.entries(value).map(([k, v]) => [k, encodeWire(v)]);
    if (Object.hasOwn(value, "$bytes") || Object.hasOwn(value, "$escaped")) return { $escaped: entries };
    return Object.fromEntries(entries);
  }
  return value;
}
function decodeWire(value) {
  if (Array.isArray(value)) return value.map(decodeWire);
  if (value && typeof value === "object") {
    const keys = Object.keys(value);
    if (keys.length === 1 && keys[0] === "$bytes") return Buffer.from(value.$bytes, "base64");
    if (keys.length === 1 && keys[0] === "$escaped") return Object.fromEntries(value.$escaped.map(([k, v]) => [k, decodeWire(v)]));
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, decodeWire(v)]));
  }
  return value;
}
class Bridge {
  constructor(python, runtime, log) {
    this.pending = new Map();
    this.next = 0;
    this.process = spawn(python, ["-u", "-m", "pixellang.workstation"], {
      cwd: runtime,
      env: { ...process.env, PYTHONPATH: runtime, PYTHONIOENCODING: "utf-8" },
      stdio: ["pipe", "pipe", "pipe"],
    });
    readline
      .createInterface({ input: this.process.stdout })
      .on("line", (line) => {
        try {
          const m = decodeWire(JSON.parse(line)),
            p = this.pending.get(m.id);
          if (p) {
            this.pending.delete(m.id);
            p.cleanup?.();
            m.error
              ? p.reject(
                  Object.assign(new Error(m.error.message), {
                    diagnostic: m.error,
                  }),
                )
              : p.resolve(m.result);
          }
        } catch (e) {
          log.appendLine(String(e));
        }
      });
    this.process.stderr.on("data", (data) => log.append(data.toString()));
    this.process.on("error", (e) => this.fail(e));
    this.process.on("exit", (code) =>
      this.fail(
        new Error(
          `PixelLang service exited (${code}). Set pixellang.pythonPath to Python 3.11+ with Pillow installed. See PixelLang output.`,
        ),
      ),
    );
  }
  fail(error) {
    this.error = error;
    for (const p of this.pending.values()) { p.cleanup?.(); p.reject(error); }
    this.pending.clear();
  }
  call(method, args = {}, options = {}) {
    if (options.signal?.aborted) return Promise.reject(Object.assign(new Error("Compilation cancelled"), { name: "AbortError" }));
    if (this.error) return Promise.reject(this.error);
    return new Promise((resolve, reject) => {
      const id = ++this.next;
      const abort = () => {
        this.pending.delete(id);
        options.signal?.removeEventListener("abort", abort);
        this.process.stdin.write(JSON.stringify({ version: 1, id: ++this.next, operation: "cancel", target: id }) + "\n");
        reject(Object.assign(new Error("Compilation cancelled"), { name: "AbortError" }));
      };
      const cleanup = () => options.signal?.removeEventListener("abort", abort);
      this.pending.set(id, { resolve, reject, cleanup });
      options.signal?.addEventListener("abort", abort, { once: true });
      this.process.stdin.write(
        JSON.stringify(encodeWire({ version: 1, id, operation: "studio", method, args, revision: args.revision })) + "\n",
        (e) => {
          if (e) {
            this.pending.delete(id);
            cleanup();
            reject(e);
          }
        },
      );
    });
  }
  dispose() {
    this.process.kill();
    this.fail(new Error("Studio service stopped"));
  }
}
module.exports = { Bridge, encodeWire, decodeWire };
