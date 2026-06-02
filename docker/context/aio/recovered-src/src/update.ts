// [recovered from /usr/local/bin/aio — esbuild bundle; transpiled JS, not original TS]
// src/update.ts
import { homedir as homedir4 } from "node:os";
import { join as join4 } from "node:path";
var AIO_DIR3 = join4(homedir4(), ".aio");
var UPDATE_CHECK_FILE = join4(AIO_DIR3, "update-check.json");
var CHECK_INTERVAL_MS = 24 * 60 * 60 * 1e3;

