// One-shot generator: turn the recovered esbuild module slices into a real,
// multi-file ESM source tree by re-adding export/import statements.
// Run with node, then delete. Output: ../aio/src/**.js
import { readFileSync, writeFileSync, mkdirSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";

const IN = resolve("recovered-src/src");
const OUT = resolve("src");

const DECL_RE = /^(?:export\s+)?(?:async\s+)?(?:function|class|const|let|var)\s+([A-Za-z0-9_$]+)/;

function walk(dir) {
  const out = [];
  for (const e of readdirSync(dir)) {
    const p = join(dir, e);
    if (statSync(p).isDirectory()) out.push(...walk(p));
    else if (p.endsWith(".ts")) out.push(p);
  }
  return out;
}

const files = walk(IN);

// ---- pass 1: build symbol -> owner map (skip esbuild interop vars) ----
const SKIP_DECL = new Set(["import_cosmiconfig"]);
const owner = new Map(); // symbol -> file abspath
const declsByFile = new Map(); // file -> Set(symbol)
for (const f of files) {
  const set = new Set();
  for (const line of readFileSync(f, "utf8").split("\n")) {
    const m = line.match(DECL_RE);
    if (m && !SKIP_DECL.has(m[1])) {
      set.add(m[1]);
      owner.set(m[1], f);
    }
  }
  declsByFile.set(f, set);
}

const importPath = (fromFile, toFile) => {
  let rel = relative(dirname(fromFile), toFile).replace(/\.ts$/, ".js");
  if (!rel.startsWith(".")) rel = "./" + rel;
  return rel;
};

// ---- pass 2: rewrite each file ----
for (const f of files) {
  let lines = readFileSync(f, "utf8").split("\n");
  // drop the recovery-notice line and the esbuild "// src/..." banner
  lines = lines.filter(
    (l) => !l.startsWith("// [recovered from") && !/^\/\/ src\//.test(l),
  );
  let body = lines.join("\n");

  const local = declsByFile.get(f);

  // foreign own-module symbols referenced in this file -> imports grouped by module
  const byModule = new Map(); // toFile -> Set(symbol)
  for (const [sym, ownFile] of owner) {
    if (ownFile === f || local.has(sym)) continue;
    const re = new RegExp(`\\b${sym.replace(/[$]/g, "\\$")}\\b`);
    if (re.test(body)) {
      if (!byModule.has(ownFile)) byModule.set(ownFile, new Set());
      byModule.get(ownFile).add(sym);
    }
  }

  const importLines = [];
  // vendor imports
  if (/\bCommand\b/.test(body)) importLines.push(`import { Command } from "commander";`);
  if (/\bsource_default\b/.test(body)) importLines.push(`import source_default from "chalk";`);
  // own-module imports (sorted for stable output)
  for (const [toFile, syms] of [...byModule].sort((a, b) => a[0].localeCompare(b[0]))) {
    const names = [...syms].sort().join(", ");
    importLines.push(`import { ${names} } from "${importPath(f, toFile)}";`);
  }

  // export every top-level declaration
  body = body
    .split("\n")
    .map((l) => {
      const m = l.match(DECL_RE);
      if (m && !SKIP_DECL.has(m[1]) && !/^export\s/.test(l)) {
        return l.replace(/^(\s*)(async\s+)?(function|class|const|let|var)\b/, "$1export $2$3");
      }
      return l;
    })
    .join("\n");

  // keep node: imports already inside body at top; prepend the new imports above everything
  const header = importLines.length ? importLines.join("\n") + "\n" : "";
  const outPath = join(OUT, relative(IN, f)).replace(/\.ts$/, ".js");
  mkdirSync(dirname(outPath), { recursive: true });
  writeFileSync(outPath, header + body);
  console.log(`${relative(OUT, outPath)}  (+${importLines.length} imports)`);
}
console.log("done");
