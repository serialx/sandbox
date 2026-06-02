// [recovered from /usr/local/bin/aio — esbuild bundle; transpiled JS, not original TS]
// src/commands/observe.ts
function parseDurationSeconds(raw, createCliError) {
  const match = raw.trim().match(/^(\d+(?:\.\d+)?)([smh]?)$/i);
  if (!match) {
    throw createCliError(`Invalid duration: ${raw}`);
  }
  const value = Number(match[1]);
  if (!Number.isFinite(value) || value <= 0) {
    throw createCliError(`Invalid duration: ${raw}`);
  }
  const unit = match[2].toLowerCase();
  if (unit === "h") return Math.round(value * 3600);
  if (unit === "m") return Math.round(value * 60);
  return Math.round(value);
}
function formatMiB(bytes) {
  if (bytes === null || bytes === void 0) return "n/a";
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}
function formatPct(value) {
  if (value === null || value === void 0) return "n/a";
  return `${value.toFixed(1)}%`;
}
function pad(value, width) {
  return String(value).padEnd(width);
}
function printObservationStatus(status) {
  console.log(`mode: ${status.mode}`);
  console.log(`running: ${status.running}`);
  if (status.session_id) console.log(`session_id: ${status.session_id}`);
  if (status.started_at) console.log(`started_at: ${status.started_at}`);
  if (status.ends_at) console.log(`ends_at: ${status.ends_at}`);
  if (status.interval_seconds !== null && status.interval_seconds !== void 0) {
    console.log(`interval_seconds: ${status.interval_seconds}`);
  }
  if (status.last_sample_at) console.log(`last_sample_at: ${status.last_sample_at}`);
  if (status.runtime_dir) console.log(`runtime_dir: ${status.runtime_dir}`);
  console.log(`report_count: ${status.report_count}`);
}
function printObservationSnapshot(snapshot) {
  console.log(`${source_default.bold("Observation")} ${snapshot.captured_at}  mode=${snapshot.mode}`);
  const cgroup = snapshot.cgroup;
  const memMax = cgroup.mem_max_bytes ? formatMiB(cgroup.mem_max_bytes) : "max";
  console.log(
    `cgroup  cpu=${formatPct(cgroup.cpu_usage_pct)}  mem=${formatMiB(cgroup.mem_current_bytes)}/${memMax} (${formatPct(cgroup.mem_usage_pct)})  oom=${cgroup.oom ?? "n/a"}  oom_kill=${cgroup.oom_kill ?? "n/a"}`
  );
  if (snapshot.disk.length > 0) {
    console.log("");
    console.log(source_default.bold("disk"));
    for (const disk of snapshot.disk) {
      console.log(
        `${disk.path}: used=${formatMiB(disk.used_bytes)} total=${formatMiB(disk.total_bytes)} usage=${formatPct(disk.usage_pct)} inode=${formatPct(disk.inode_usage_pct)}`
      );
    }
  }
  if (snapshot.top_processes.length > 0) {
    console.log("");
    console.log(source_default.bold("processes"));
    console.log(
      `${pad("pid", 8)}${pad("user", 10)}${pad("proc", 24)}${pad("rss", 12)}${pad("cpu", 8)}${pad("thr", 8)}${pad("fds", 8)}state`
    );
    for (const proc of snapshot.top_processes) {
      console.log(
        `${pad(proc.pid, 8)}${pad(proc.user, 10)}${pad(proc.comm, 24)}${pad(proc.rss_mib.toFixed(1), 12)}${pad(proc.cpu_pct?.toFixed(1) ?? "n/a", 8)}${pad(proc.threads ?? "n/a", 8)}${pad(proc.fds ?? "n/a", 8)}${proc.state ?? "n/a"}`
      );
    }
  }
  if (snapshot.recent_events.length > 0) {
    console.log("");
    console.log(source_default.bold("events"));
    for (const event of snapshot.recent_events.slice(-5)) {
      console.log(`${event.ts}  ${event.type}  ${event.message}`);
    }
  }
}
function printObservationStart(result) {
  console.log(`session_id: ${result.session_id}`);
  console.log(`mode: ${result.mode}`);
  console.log(`started_at: ${result.started_at}`);
  if (result.ends_at) console.log(`ends_at: ${result.ends_at}`);
  console.log(`interval_seconds: ${result.interval_seconds}`);
  console.log(`runtime_dir: ${result.runtime_dir}`);
}
function printObservationStop(result) {
  console.log(`session_id: ${result.session_id}`);
  console.log(`stopped: ${result.stopped}`);
  console.log(`report_ready: ${result.report_ready}`);
}
function printObservationReports(reports) {
  if (reports.length === 0) {
    console.log(source_default.dim("(empty)"));
    return;
  }
  console.log(
    `${pad("report_id", 28)}${pad("session_id", 24)}${pad("reason", 12)}${pad("size", 12)}created_at`
  );
  for (const report of reports) {
    console.log(
      `${pad(report.report_id, 28)}${pad(report.session_id ?? "-", 24)}${pad(report.reason, 12)}${pad(formatMiB(report.size_bytes), 12)}${report.created_at}`
    );
  }
}
function attachObservationCommands(sandbox, deps) {
  const { getClient: getClient2, getOpts, createCliError, inSandbox, runLocalObserve } = deps;
  const observe = sandbox.command("observe").description("Diagnostic observation and report capture");
  observe.command("status").description("Show observation service status").action(async () => {
    const resp = await getClient2().get("/v1/sandbox/observe/status");
    const format = detectFormat(getOpts().output);
    if (format === "json") {
      printResponse(resp, format);
      return;
    }
    printObservationStatus(resp.data);
  });
  observe.command("once").description("Collect one live observation snapshot").option("--rows <n>", "Number of processes to show", (v) => parseInt(v, 10), 10).action(async (opts) => {
    const resp = await getClient2().get("/v1/sandbox/observe/live", {
      top_rows: String(opts.rows)
    });
    const format = detectFormat(getOpts().output);
    if (format === "json") {
      printResponse(resp, format);
      return;
    }
    printObservationSnapshot(resp.data);
  });
  observe.command("live").description("Show a live observation snapshot").option("--rows <n>", "Number of processes to show", (v) => parseInt(v, 10), 10).action(async (opts) => {
    const resp = await getClient2().get("/v1/sandbox/observe/live", {
      top_rows: String(opts.rows)
    });
    const format = detectFormat(getOpts().output);
    if (format === "json") {
      printResponse(resp, format);
      return;
    }
    printObservationSnapshot(resp.data);
  });
  observe.command("start").description("Start a background observation session").option("--mode <mode>", "guardrail or capture", "capture").option("--key <idempotency-key>", "Idempotency key for safe retries").option("--duration <duration>", "Duration like 30s, 15m, 1h").option("--interval <seconds>", "Sampling interval in seconds", parseFloat).option("--no-processes", "Skip process sampling").option("--no-disk", "Skip disk sampling").action(async (opts) => {
    if (opts.mode !== "guardrail" && opts.mode !== "capture") {
      throw createCliError(`Unsupported mode: ${opts.mode}`);
    }
    const durationSeconds = opts.duration ? parseDurationSeconds(opts.duration, createCliError) : opts.mode === "capture" ? 15 * 60 : void 0;
    const resp = await getClient2().post("/v1/sandbox/observe/start", {
      mode: opts.mode,
      idempotency_key: opts.key,
      duration_seconds: durationSeconds,
      interval_seconds: opts.interval,
      include_processes: Boolean(opts.processes),
      include_disk: Boolean(opts.disk)
    });
    const format = detectFormat(getOpts().output);
    if (format === "json") {
      printResponse(resp, format);
      return;
    }
    printObservationStart(resp.data);
  });
  observe.command("stop").description("Stop the active observation session").option("--session-id <session-id>", "Expected active session id").action(async (opts) => {
    const resp = await getClient2().post("/v1/sandbox/observe/stop", {
      session_id: opts.sessionId
    });
    const format = detectFormat(getOpts().output);
    if (format === "json") {
      printResponse(resp, format);
      return;
    }
    printObservationStop(resp.data);
  });
  observe.command("export").description("Export the current or most recent observation report").option("--key <idempotency-key>", "Idempotency key for safe retries").option("--session-id <session-id>", "Observation session id to export").option("--reason <reason>", "Export reason", "manual").option("-o, --output <local-path>", "Write the report tarball to a local path").action(async (opts) => {
    const resp = await getClient2().post("/v1/sandbox/observe/export", {
      idempotency_key: opts.key,
      session_id: opts.sessionId,
      reason: opts.reason
    });
    if (opts.output) {
      const { buffer } = await getClient2().getBinary(
        `/v1/sandbox/observe/reports/${encodeURIComponent(resp.data.report_id)}`
      );
      await writeFile3(opts.output, buffer);
      console.log(`Downloaded to ${opts.output}`);
      return;
    }
    const format = detectFormat(getOpts().output);
    if (format === "json") {
      printResponse(resp, format);
      return;
    }
    printObservationReports([resp.data]);
  });
  observe.command("reports").description("List exported observation reports").action(async () => {
    const resp = await getClient2().get("/v1/sandbox/observe/reports");
    const format = detectFormat(getOpts().output);
    if (format === "json") {
      printResponse(resp, format);
      return;
    }
    printObservationReports(resp.data);
  });
  const report = observe.command("report").description("Manage exported observation reports");
  report.command("download").description("Download an exported observation report").argument("<report-id>", "Report id").option("-o, --output <local-path>", "Local file path").action(async (reportId, opts) => {
    const outFile = opts.output || `${basename(reportId)}.tar.gz`;
    const { buffer } = await getClient2().getBinary(
      `/v1/sandbox/observe/reports/${encodeURIComponent(reportId)}`
    );
    await writeFile3(outFile, buffer);
    console.log(`Downloaded to ${outFile}`);
  });
  report.command("delete").description("Delete an exported observation report").argument("<report-id>", "Report id").action(async (reportId) => {
    const resp = await getClient2().delete(
      `/v1/sandbox/observe/reports/${encodeURIComponent(reportId)}`
    );
    const format = detectFormat(getOpts().output);
    if (format === "json") {
      printResponse(resp, format);
      return;
    }
    printObservationReports([resp.data]);
  });
  if (inSandbox) {
    observe.addCommand(createObserveLocalCommand(inSandbox, runLocalObserve));
  }
}

