// [recovered from /usr/local/bin/aio — esbuild bundle; transpiled JS, not original TS]
// src/context.ts
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
var AIO_DIR = join(homedir(), ".aio");
var CONTEXT_FILE = join(AIO_DIR, "context.json");
var DEFAULT_CONTEXT = { provider: "local" };
function loadContext() {
  try {
    const raw = readFileSync(CONTEXT_FILE, "utf-8");
    return { ...DEFAULT_CONTEXT, ...JSON.parse(raw) };
  } catch {
    return DEFAULT_CONTEXT;
  }
}
function resolveContext(flags) {
  const saved = loadContext();
  return {
    provider: flags.provider ?? saved.provider,
    apiUrl: flags.apiUrl ?? saved.apiUrl,
    psm: flags.psm ?? saved.psm,
    region: flags.region ?? saved.region,
    session: flags.session ?? saved.session
  };
}

