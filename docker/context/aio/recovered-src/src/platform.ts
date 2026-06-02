// [recovered from /usr/local/bin/aio — esbuild bundle; transpiled JS, not original TS]
// src/platform.ts
import { execFileSync as execFileSync2, spawnSync } from "node:child_process";
import { platform as osPlatform, arch as osArch } from "node:os";
var PLATFORM = osPlatform();
var ARCH = osArch();
var IS_WINDOWS = PLATFORM === "win32";
var IS_MACOS = PLATFORM === "darwin";
function hasCommand(cmd) {
  try {
    if (IS_WINDOWS) {
      execFileSync2("where", [cmd], { stdio: "pipe" });
    } else {
      execFileSync2("which", [cmd], { stdio: "pipe" });
    }
    return true;
  } catch {
    return false;
  }
}
function canExec(cmd, args, timeoutMs = 1e4) {
  try {
    execFileSync2(resolveCmd(cmd), args, { stdio: "pipe", timeout: timeoutMs });
    return true;
  } catch {
    return false;
  }
}
function execCmd(cmd, args, timeoutMs = 6e4) {
  return execFileSync2(resolveCmd(cmd), args, { encoding: "utf-8", timeout: timeoutMs }).trim();
}
function spawnCmd(cmd, args, timeoutMs = 3e5) {
  return spawnSync(resolveCmd(cmd), args, { stdio: "inherit", timeout: timeoutMs });
}
var WIN_CMD_SUFFIX = /* @__PURE__ */ new Set(["npm", "npx", "pnpm", "yarn", "bunx"]);
function resolveCmd(cmd) {
  if (IS_WINDOWS && WIN_CMD_SUFFIX.has(cmd)) {
    return `${cmd}.cmd`;
  }
  return cmd;
}
function detectContainerRuntime() {
  if (hasCommand("docker") && canExec("docker", ["info"])) {
    return { bin: "docker", runtime: "docker" };
  }
  if (IS_MACOS) {
    if (hasCommand("nerdctl") && canExec("nerdctl", ["info"])) {
      return { bin: "nerdctl", runtime: "colima" };
    }
    if (hasCommand("nerdctl") && hasCommand("colima")) {
      console.log("Starting Colima...");
      const result = spawnCmd("colima", ["start", "--runtime", "containerd", "--cpu", "2", "--memory", "4"], 12e4);
      if (result.status === 0 && canExec("nerdctl", ["info"])) {
        return { bin: "nerdctl", runtime: "colima" };
      }
    }
  }
  const hint = IS_MACOS ? "Run 'aio install' to set up Docker or Colima." : IS_WINDOWS ? "Install Docker Desktop from https://www.docker.com/products/docker-desktop/" : "Install Docker Engine: https://docs.docker.com/engine/install/";
  throw new Error(`No container runtime found. ${hint}`);
}

