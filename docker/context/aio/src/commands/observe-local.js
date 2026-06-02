import { Command } from "commander";
import { CliError } from "../client.js";
import { execFileSync } from "node:child_process";
import { readFileSync as readFileSync2 } from "node:fs";
import { userInfo } from "node:os";
import { setTimeout as sleep } from "node:timers/promises";
export var DEFAULT_INTERVAL_SECONDS = 2;
export var DEFAULT_HISTORY_POINTS = 30;
export var DEFAULT_TOP_ROWS = 8;
export var DEFAULT_WARMUP_MS = 250;
export var DEFAULT_CLOCK_TICKS_PER_SECOND = 100;
export var PS_ARGS = ["-eo", "pid=,user=,ppid=,rss=,comm="];
export var CLEAR_SCREEN = "\x1B[2J\x1B[H";
export var UNKNOWN_BAR_CHAR = "?";
export var FILLED_BAR_CHAR = "#";
export var EMPTY_BAR_CHAR = "-";
export function createObserveLocalCommand(inSandbox, runObserveLocalImpl = runObserveLocal) {
  return new Command("local").alias("top").description("Run an in-sandbox local process watch without using the observe API").option(
    "-i, --interval <seconds>",
    "Sampling interval in seconds",
    parsePositiveFloat,
    DEFAULT_INTERVAL_SECONDS
  ).option(
    "--history <points>",
    "Number of samples to retain for the trend graph",
    parsePositiveInt,
    DEFAULT_HISTORY_POINTS
  ).option(
    "--rows <count>",
    "Number of processes to show",
    parsePositiveInt,
    DEFAULT_TOP_ROWS
  ).option("--once", "Render one snapshot and exit", false).action(async (opts) => {
    await runObserveLocalImpl({
      inSandbox,
      intervalSeconds: opts.interval,
      historyPoints: opts.history,
      topRows: opts.rows,
      once: opts.once
    });
  });
}
export async function runObserveLocal(options) {
  ensureSupportedEnvironment(options.inSandbox);
  const history = createHistoryState(options.historyPoints);
  const samplerState = createSamplerState();
  const interactive = process.stdout.isTTY && !options.once;
  const sleepAbortController = new AbortController();
  let stopped = false;
  const stop = () => {
    stopped = true;
    sleepAbortController.abort();
  };
  process.once("SIGINT", stop);
  process.once("SIGTERM", stop);
  try {
    const baseline = collectSnapshot(samplerState);
    pushHistory(history, baseline);
    const warmupMs = Math.min(
      Math.max(1, Math.round(options.intervalSeconds * 1e3)),
      DEFAULT_WARMUP_MS
    );
    try {
      await sleep(warmupMs, void 0, { signal: sleepAbortController.signal });
    } catch (error) {
      if (!(stopped && error instanceof Error && error.name === "AbortError")) {
        throw error;
      }
    }
    do {
      const startedAt = Date.now();
      const snapshot = collectSnapshot(samplerState);
      pushHistory(history, snapshot);
      const output = renderObserveLocalDashboard(snapshot, history, {
        intervalSeconds: options.intervalSeconds,
        topRows: options.topRows
      });
      if (!interactive) {
        process.stdout.write(`${output}
`);
        break;
      }
      process.stdout.write(CLEAR_SCREEN);
      process.stdout.write(output);
      process.stdout.write("\n");
      const elapsedMs = Date.now() - startedAt;
      const sleepMs = Math.max(0, Math.round(options.intervalSeconds * 1e3) - elapsedMs);
      if (sleepMs > 0) {
        try {
          await sleep(sleepMs, void 0, { signal: sleepAbortController.signal });
        } catch (error) {
          if (!(stopped && error instanceof Error && error.name === "AbortError")) {
            throw error;
          }
        }
      }
    } while (!stopped);
  } finally {
    process.removeListener("SIGINT", stop);
    process.removeListener("SIGTERM", stop);
    if (interactive) {
      process.stdout.write("\n");
    }
  }
}
export function createHistoryState(maxPoints) {
  return {
    cgroupMemoryMiB: [],
    cgroupUsagePct: [],
    processes: /* @__PURE__ */ new Map(),
    maxPoints
  };
}
export function pushHistory(history, snapshot) {
  pushBounded(history.cgroupMemoryMiB, bytesToMiB(snapshot.cgroup.currentBytes), history.maxPoints);
  if (snapshot.cgroup.usagePct !== null) {
    pushBounded(history.cgroupUsagePct, snapshot.cgroup.usagePct, history.maxPoints);
  }
  for (const entry of history.processes.values()) {
    entry.misses += 1;
  }
  for (const processSnapshot of snapshot.processes) {
    const existing = history.processes.get(processSnapshot.pid);
    if (existing) {
      existing.comm = processSnapshot.comm;
      existing.user = processSnapshot.user;
      existing.misses = 0;
      pushBounded(existing.rssMiB, kbToMiB(processSnapshot.rssKb), history.maxPoints);
      continue;
    }
    history.processes.set(processSnapshot.pid, {
      comm: processSnapshot.comm,
      user: processSnapshot.user,
      misses: 0,
      rssMiB: [kbToMiB(processSnapshot.rssKb)]
    });
  }
  for (const [pid, entry] of history.processes.entries()) {
    if (entry.misses >= history.maxPoints) {
      history.processes.delete(pid);
    }
  }
}
export function renderObserveLocalDashboard(snapshot, history, options) {
  const width = Math.max(options.width ?? process.stdout.columns ?? 120, 88);
  const summaryBarWidth = Math.max(10, Math.min(24, width - 94));
  const processBarWidth = Math.max(10, Math.min(18, width - 64));
  const lines = [];
  const cgroupUsageText = formatCgroupUsage(snapshot.cgroup);
  const cgroupPctText = formatPercent(snapshot.cgroup.usagePct);
  const cgroupLevel = classifyUsageLevel(snapshot.cgroup.usagePct);
  const cgroupTrend = snapshot.cgroup.maxBytes === null ? summarizeTrend(history.cgroupMemoryMiB, "MiB", 4) : summarizeTrend(history.cgroupUsagePct, "%", 0.5);
  lines.push(
    `AIO Sandbox Observe Local  ${formatTimestamp(snapshot.capturedAt)}  user=${snapshot.displayUser}  interval=${options.intervalSeconds}s`
  );
  lines.push(
    `memory  used=${cgroupUsageText.current}  limit=${cgroupUsageText.limit}  pct=${cgroupPctText}  bar=${renderPercentBar(snapshot.cgroup.usagePct, summaryBarWidth)}  level=${cgroupLevel}  oom=${formatOptionalInteger(snapshot.cgroup.oomCount)}  oom_kill=${formatOptionalInteger(snapshot.cgroup.oomKillCount)}`
  );
  lines.push(
    `trend   dir=${cgroupTrend.direction}  delta=${cgroupTrend.deltaText}  window=${cgroupTrend.points}  min=${cgroupTrend.minText}  max=${cgroupTrend.maxText}`
  );
  lines.push("");
  lines.push("pid      proc             rss_mib   rss%   cpu%  rss_bar               trend");
  for (const processSnapshot of snapshot.processes.slice(0, options.topRows)) {
    const processHistory = history.processes.get(processSnapshot.pid);
    const rssMiB = kbToMiB(processSnapshot.rssKb);
    const rssPct = calculateProcessMemoryPct(processSnapshot.rssKb, snapshot.cgroup.maxBytes);
    const trend = summarizeTrend(processHistory?.rssMiB ?? [], "MiB", 4).label;
    lines.push(
      `${padLeft(String(processSnapshot.pid), 6)} ${padRight(truncate(processSnapshot.comm, 16), 16)} ${padLeft(rssMiB.toFixed(1), 9)} ${padLeft(formatPercentCompact(rssPct), 6)} ${padLeft(processSnapshot.cpuPct.toFixed(1), 6)}  ${renderPercentBar(rssPct, processBarWidth)}  ${trend}`
    );
  }
  if (snapshot.processes.length === 0) {
    lines.push("no current-user processes found");
  }
  return lines.join("\n");
}
export function createSamplerState() {
  return {
    clockTicksPerSecond: readClockTicksPerSecond(),
    displayUser: resolveDisplayUser(),
    previousCapturedAtMs: null,
    previousCpuTicksByPid: /* @__PURE__ */ new Map()
  };
}
export function collectSnapshot(state) {
  const capturedAt = /* @__PURE__ */ new Date();
  const capturedAtMs = capturedAt.getTime();
  const rawProcesses = parsePsOutput(readPsOutput()).filter(
    (sample) => sample.pid !== process.pid && sample.user === state.displayUser
  );
  const cpuTicksByPid = readCpuTicksByPid(rawProcesses.map((sample) => sample.pid));
  const elapsedSeconds = state.previousCapturedAtMs === null ? null : Math.max(1e-3, (capturedAtMs - state.previousCapturedAtMs) / 1e3);
  const processes = rawProcesses.map((sample) => ({
    ...sample,
    cpuPct: calculateCpuPct(sample.pid, cpuTicksByPid, state, elapsedSeconds)
  })).sort(
    (left, right) => right.rssKb - left.rssKb || right.cpuPct - left.cpuPct || left.comm.localeCompare(right.comm)
  );
  state.previousCapturedAtMs = capturedAtMs;
  state.previousCpuTicksByPid = cpuTicksByPid;
  return {
    capturedAt,
    cgroup: readCgroupSnapshot(),
    displayUser: state.displayUser,
    processes
  };
}
export function calculateCpuPct(pid, cpuTicksByPid, state, elapsedSeconds) {
  if (elapsedSeconds === null) {
    return 0;
  }
  const currentTicks = cpuTicksByPid.get(pid);
  const previousTicks = state.previousCpuTicksByPid.get(pid);
  if (currentTicks === void 0 || previousTicks === void 0) {
    return 0;
  }
  const deltaTicks = currentTicks - previousTicks;
  if (deltaTicks <= 0) {
    return 0;
  }
  return deltaTicks / state.clockTicksPerSecond / elapsedSeconds * 100;
}
export function ensureSupportedEnvironment(inSandbox) {
  if (process.platform !== "linux") {
    throw new CliError("`aio sandbox observe local` only supports Linux.");
  }
  if (!inSandbox) {
    throw new CliError(
      "`aio sandbox observe local` is intended to run inside a sandbox because it reads local /proc and cgroup state."
    );
  }
}
export function parsePsOutput(output) {
  return output.split("\n").map((line) => line.trim()).filter(Boolean).map(parsePsLine).filter((sample) => sample !== null);
}
export function parsePsLine(line) {
  const match = line.match(/^(\d+)\s+(\S+)\s+(\d+)\s+(\d+)\s+(\S+)$/);
  if (!match) {
    return null;
  }
  const pid = Number.parseInt(match[1], 10);
  const user = match[2];
  const ppid = Number.parseInt(match[3], 10);
  const rssKb = Number.parseInt(match[4], 10);
  const comm = match[5];
  if (!Number.isFinite(pid) || !Number.isFinite(ppid) || !Number.isFinite(rssKb) || user.length === 0 || comm.length === 0) {
    return null;
  }
  return {
    pid,
    user,
    ppid,
    rssKb,
    comm
  };
}
export function readPsOutput() {
  try {
    return execFileSync("ps", PS_ARGS, {
      encoding: "utf8",
      maxBuffer: 16 * 1024 * 1024
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new CliError(`Failed to collect process stats with ps: ${message}`);
  }
}
export function readCpuTicksByPid(pids) {
  const cpuTicksByPid = /* @__PURE__ */ new Map();
  for (const pid of pids) {
    const cpuTicks = readProcessCpuTicks(pid);
    if (cpuTicks !== null) {
      cpuTicksByPid.set(pid, cpuTicks);
    }
  }
  return cpuTicksByPid;
}
export function readProcessCpuTicks(pid) {
  try {
    const raw = readFileSync2(`/proc/${pid}/stat`, "utf8").trim();
    const closingParenIndex = raw.lastIndexOf(")");
    if (closingParenIndex < 0 || closingParenIndex + 2 >= raw.length) {
      return null;
    }
    const fields = raw.slice(closingParenIndex + 2).trim().split(/\s+/);
    if (fields.length < 13) {
      return null;
    }
    const utime = parseInteger(fields[11]);
    const stime = parseInteger(fields[12]);
    if (utime === null || stime === null) {
      return null;
    }
    return utime + stime;
  } catch {
    return null;
  }
}
export function readClockTicksPerSecond() {
  try {
    const output = execFileSync("getconf", ["CLK_TCK"], { encoding: "utf8" }).trim();
    const parsed = Number.parseInt(output, 10);
    if (Number.isFinite(parsed) && parsed > 0) {
      return parsed;
    }
  } catch {
  }
  return DEFAULT_CLOCK_TICKS_PER_SECOND;
}
export function resolveDisplayUser() {
  const envUser = process.env.USER?.trim();
  if (envUser) {
    return envUser;
  }
  try {
    return userInfo().username;
  } catch {
    return "unknown";
  }
}
export function readCgroupSnapshot() {
  const currentText = readFirstFile(buildCgroupFileCandidates("memory.current", "memory.usage_in_bytes"));
  const maxText = readFirstFile(buildCgroupFileCandidates("memory.max", "memory.limit_in_bytes"));
  const eventText = readFirstFile(buildCgroupFileCandidates("memory.events", "memory.events.local"));
  const currentBytes = parseInteger(currentText) ?? 0;
  const rawMax = maxText?.trim();
  let maxBytes = null;
  if (rawMax !== void 0 && rawMax !== "max") {
    const parsedMax = parseInteger(rawMax);
    if (parsedMax !== null && parsedMax > 0 && parsedMax < 1099511627776) {
      maxBytes = parsedMax;
    }
  }
  const events = parseKeyValueFile(eventText);
  return {
    currentBytes,
    maxBytes,
    usagePct: maxBytes !== null ? currentBytes / maxBytes * 100 : null,
    oomCount: events.oom ?? null,
    oomKillCount: events.oom_kill ?? null
  };
}
export function buildCgroupFileCandidates(v2Name, v1Name) {
  const candidates = [];
  for (const basePath of resolveCgroupBasePaths()) {
    candidates.push(joinPath(basePath, v2Name));
    candidates.push(joinPath(basePath, v1Name));
  }
  candidates.push(`/sys/fs/cgroup/${v2Name}`);
  candidates.push(`/sys/fs/cgroup/${v1Name}`);
  candidates.push(`/sys/fs/cgroup/memory/${v2Name}`);
  candidates.push(`/sys/fs/cgroup/memory/${v1Name}`);
  return [...new Set(candidates)];
}
export function resolveCgroupBasePaths() {
  const basePaths = [];
  try {
    const raw = readFileSync2("/proc/self/cgroup", "utf8");
    for (const line of raw.split("\n")) {
      const parts = line.trim().split(":");
      if (parts.length !== 3) {
        continue;
      }
      const controllers = parts[1];
      const cgroupPath = parts[2] || "/";
      if (controllers === "") {
        basePaths.push(joinPath("/sys/fs/cgroup", cgroupPath));
      } else if (controllers.split(",").includes("memory")) {
        basePaths.push(joinPath("/sys/fs/cgroup/memory", cgroupPath));
      }
    }
  } catch {
  }
  basePaths.push("/sys/fs/cgroup");
  basePaths.push("/sys/fs/cgroup/memory");
  return [...new Set(basePaths)];
}
export function joinPath(basePath, childPath) {
  const joined = `${basePath.replace(/\/+$/, "")}/${childPath.replace(/^\/+/, "")}`;
  return joined.replace(/\/{2,}/g, "/");
}
export function readFirstFile(paths) {
  for (const path of paths) {
    try {
      return readFileSync2(path, "utf8").trim();
    } catch {
      continue;
    }
  }
  return null;
}
export function parseKeyValueFile(raw) {
  if (!raw) {
    return {};
  }
  const values = {};
  for (const line of raw.split("\n")) {
    const [key, value] = line.trim().split(/\s+/, 2);
    const parsed = parseInteger(value);
    if (key && parsed !== null) {
      values[key] = parsed;
    }
  }
  return values;
}
export function formatCgroupUsage(snapshot) {
  const current = bytesToMiB(snapshot.currentBytes).toFixed(1);
  if (snapshot.maxBytes === null || snapshot.usagePct === null) {
    return {
      current: `${current}MiB`,
      limit: "n/a"
    };
  }
  return {
    current: `${current}MiB`,
    limit: `${bytesToMiB(snapshot.maxBytes).toFixed(1)}MiB`
  };
}
export function formatTimestamp(date) {
  const year = date.getFullYear();
  const month = padLeft(String(date.getMonth() + 1), 2, "0");
  const day = padLeft(String(date.getDate()), 2, "0");
  const hour = padLeft(String(date.getHours()), 2, "0");
  const minute = padLeft(String(date.getMinutes()), 2, "0");
  const second = padLeft(String(date.getSeconds()), 2, "0");
  return `${year}-${month}-${day} ${hour}:${minute}:${second}`;
}
export function formatOptionalInteger(value) {
  return value === null ? "n/a" : String(value);
}
export function formatPercent(value) {
  return value === null ? "n/a" : `${value.toFixed(1)}%`;
}
export function formatPercentCompact(value) {
  return value === null ? "n/a" : `${value.toFixed(1)}%`;
}
export function calculateProcessMemoryPct(rssKb, maxBytes) {
  if (maxBytes === null || maxBytes <= 0) {
    return null;
  }
  return rssKb * 1024 / maxBytes * 100;
}
export function renderPercentBar(pct, width) {
  if (width <= 0) {
    return "[]";
  }
  if (pct === null || !Number.isFinite(pct)) {
    return `[${UNKNOWN_BAR_CHAR.repeat(width)}]`;
  }
  const clamped = Math.max(0, Math.min(pct, 100));
  const filledWidth = Math.round(clamped / 100 * width);
  return `[${FILLED_BAR_CHAR.repeat(filledWidth)}${EMPTY_BAR_CHAR.repeat(width - filledWidth)}]`;
}
export function classifyUsageLevel(pct) {
  if (pct === null) {
    return "n/a";
  }
  if (pct >= 90) {
    return "crit";
  }
  if (pct >= 75) {
    return "warn";
  }
  return "ok";
}
export function summarizeTrend(values, unitSuffix, flatThreshold) {
  if (values.length === 0) {
    return {
      direction: "n/a",
      deltaText: "n/a",
      minText: "n/a",
      maxText: "n/a",
      points: 0,
      label: "n/a"
    };
  }
  const first = values[0];
  const last = values[values.length - 1];
  const rawDelta = last - first;
  const normalizedDelta = Math.abs(rawDelta) <= flatThreshold ? 0 : rawDelta;
  const direction = normalizedDelta === 0 ? "flat" : normalizedDelta > 0 ? "up" : "down";
  const deltaText = formatSignedValue(normalizedDelta, unitSuffix);
  const label = `${direction} ${deltaText}`;
  return {
    direction,
    deltaText,
    minText: formatUnsignedValue(Math.min(...values), unitSuffix),
    maxText: formatUnsignedValue(Math.max(...values), unitSuffix),
    points: values.length,
    label
  };
}
export function formatSignedValue(value, unitSuffix) {
  const sign = value >= 0 ? "+" : "-";
  return `${sign}${Math.abs(value).toFixed(1)}${unitSuffix}`;
}
export function formatUnsignedValue(value, unitSuffix) {
  return `${value.toFixed(1)}${unitSuffix}`;
}
export function parsePositiveFloat(value) {
  const parsed = Number.parseFloat(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new CliError(`Expected a positive number, got: ${value}`);
  }
  return parsed;
}
export function parsePositiveInt(value) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new CliError(`Expected a positive integer, got: ${value}`);
  }
  return parsed;
}
export function parseInteger(value) {
  if (value === void 0 || value === null) {
    return null;
  }
  const parsed = Number.parseInt(value.trim(), 10);
  return Number.isFinite(parsed) ? parsed : null;
}
export function pushBounded(values, value, maxPoints) {
  values.push(value);
  if (values.length > maxPoints) {
    values.splice(0, values.length - maxPoints);
  }
}
export function truncate(value, maxWidth) {
  if (value.length <= maxWidth) {
    return value;
  }
  return `${value.slice(0, Math.max(0, maxWidth - 3))}...`;
}
export function padLeft(value, width, fill = " ") {
  return value.padStart(width, fill);
}
export function padRight(value, width) {
  return value.padEnd(width, " ");
}
export function bytesToMiB(bytes) {
  return bytes / 1024 / 1024;
}
export function kbToMiB(kb) {
  return kb / 1024;
}

