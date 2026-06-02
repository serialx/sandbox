import { homedir as homedir2 } from "node:os";
import { join as join2 } from "node:path";
export var AIO_DIR2 = join2(homedir2(), ".aio");
export var CREDENTIALS_FILE = join2(AIO_DIR2, "credentials.json");

