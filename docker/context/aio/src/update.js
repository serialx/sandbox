import { homedir as homedir4 } from "node:os";
import { join as join4 } from "node:path";
export var AIO_DIR3 = join4(homedir4(), ".aio");
export var UPDATE_CHECK_FILE = join4(AIO_DIR3, "update-check.json");
export var CHECK_INTERVAL_MS = 24 * 60 * 60 * 1e3;

