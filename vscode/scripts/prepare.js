const fs = require("node:fs"),
  path = require("node:path");
const root = path.resolve(__dirname, "../.."),
  dest = path.join(root, "vscode/runtime/pixellang");
fs.copyFileSync(path.join(root, "LICENSE"), path.join(root, "vscode/LICENSE"));
fs.copyFileSync(path.join(root, "assets/branding/icon.png"), path.join(root, "vscode/media/icon.png"));
fs.rmSync(dest, { recursive: true, force: true });
fs.mkdirSync(dest, { recursive: true });
for (const name of fs.readdirSync(path.join(root, "pixellang")))
  if (name.endsWith(".py"))
    fs.copyFileSync(path.join(root, "pixellang", name), path.join(dest, name));

fs.cpSync(path.join(root, "pixellang/stdlib"), path.join(dest, "stdlib"), { recursive: true });

fs.cpSync(path.join(root, "pixellang/artifacts"), path.join(dest, "artifacts"), { recursive: true });
