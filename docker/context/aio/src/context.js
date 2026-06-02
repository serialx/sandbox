import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
export var AIO_DIR = join(homedir(), ".aio");
export var CONTEXT_FILE = join(AIO_DIR, "context.json");
export var DEFAULT_CONTEXT = { provider: "local" };
export function loadContext() {
  try {
    const raw = readFileSync(CONTEXT_FILE, "utf-8");
    return { ...DEFAULT_CONTEXT, ...JSON.parse(raw) };
  } catch {
    return DEFAULT_CONTEXT;
  }
}
export function resolveContext(flags) {
  const saved = loadContext();
  return {
    provider: flags.provider ?? saved.provider,
    apiUrl: flags.apiUrl ?? saved.apiUrl,
    psm: flags.psm ?? saved.psm,
    region: flags.region ?? saved.region,
    session: flags.session ?? saved.session
  };
}

