// Bundles the re-authored aio CLI into a single self-contained node script,
// reproducing the shape of the original /usr/local/bin/aio (esbuild, ESM,
// shebang banner). Top-level await in src/index.js requires the ESM format.
import { build } from "esbuild";
import { chmodSync } from "node:fs";

await build({
  entryPoints: ["src/index.js"],
  bundle: true,
  platform: "node",
  format: "esm",
  target: "node18",
  outfile: "dist/aio.js",
  legalComments: "none",
  // ESM output bundling CJS deps (commander/cosmiconfig) needs a require shim
  // and __dirname/__filename for any dep that uses them.
  banner: {
    js: [
      "#!/usr/bin/env node",
      "import { createRequire as __createRequire } from 'node:module';",
      "import { fileURLToPath as __fileURLToPath } from 'node:url';",
      "import { dirname as __pathDirname } from 'node:path';",
      "const require = __createRequire(import.meta.url);",
      "const __filename = __fileURLToPath(import.meta.url);",
      "const __dirname = __pathDirname(__filename);",
    ].join("\n"),
  },
});

chmodSync("dist/aio.js", 0o755);
console.log("built dist/aio.js");
