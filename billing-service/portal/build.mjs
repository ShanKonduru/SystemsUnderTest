// Builds dist/ for the /portal page: the TypeScript in src/ with its types stripped by Node (no compiler, no npm
// packages), plus the static files in page/. Run from anywhere: `node portal/build.mjs`.
import { cpSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const dist = join(root, "dist");
rmSync(dist, { recursive: true, force: true });
mkdirSync(dist);
for (const name of readdirSync(join(root, "src")).filter((n) => n.endsWith(".ts"))) {
  const js = stripTypeScriptTypes(readFileSync(join(root, "src", name), "utf8"))
    .replace(/(from\s+["']\.\/[^"']+)\.ts(["'])/g, "$1.js$2");
  writeFileSync(join(dist, name.replace(/\.ts$/, ".js")), js);
}
cpSync(join(root, "page"), dist, { recursive: true });
console.log(`built ${dist}`);
