import { Command } from "commander";
import source_default from "chalk";
import { CliError } from "../client.js";
import { attachObservationCommands } from "./observe.js";
import { loadConfig } from "../config.js";
import { resolveContext } from "../context.js";
import { getClient } from "../index.js";
import { detectFormat, printResponse } from "../output.js";
import { ContainerProvider } from "../providers/container.js";
export var IS_SANDBOX_BUILD = true ? true : false;
export function getProviderFromContext(flagOverrides) {
  const ctx = resolveContext(flagOverrides ?? {});
  if (ctx.provider === "aipaas") {
    if (!ctx.psm) {
      throw new Error("PSM is required for aipaas provider. Run: aio use <psm>");
    }
    return new (void 0)({
      psm: ctx.psm,
      region: ctx.region?.toUpperCase() ?? "CN"
    });
  }
  if (ctx.provider === "boxlite") {
    return new (void 0)({ region: ctx.region?.toLowerCase() });
  }
  return ContainerProvider.detect();
}
export function createSandboxCommand(getClient2, getOpts, in_sandbox2, overrides = {}) {
  const sandbox = new Command("sandbox").description("Sandbox environment management");
  sandbox.command("info").description("Get sandbox information").action(async () => {
    const resp = await getClient2().get("/v1/sandbox");
    printResponse(resp, detectFormat(getOpts().output));
  });
  sandbox.command("packages").description("List installed packages").option("--python", "List Python packages").option("--nodejs", "List Node.js packages").action(async (opts) => {
    const lang = opts.nodejs ? "nodejs" : "python";
    const resp = await getClient2().get(`/v1/sandbox/packages/${lang}`);
    printResponse(resp, detectFormat(getOpts().output));
  });
  attachObservationCommands(sandbox, {
    getClient: getClient2,
    getOpts,
    createCliError: (message) => new CliError(message),
    inSandbox: in_sandbox2,
    runLocalObserve: overrides.runLocalObserve
  });
  if (!IS_SANDBOX_BUILD && !in_sandbox2) {
    sandbox.command("create").description("Create and start a new sandbox container").argument("[name]", "Sandbox name", "default").option("--port <port>", "Host port to map (local only)", (v) => parseInt(v, 10), 8080).option("--image <image>", "Container image", "sandbox:latest").option("--cpus <cpus>", "CPU limit (e.g. 2)").option("--memory <mem>", "Memory limit (e.g. 4g)").option("--shm-size <size>", "Shared memory size", "4g").option("-e, --env <key=value...>", "Environment variables").option("-v, --volume <src:dst...>", "Volume mounts (local only)").option("--ttl <seconds>", "Session TTL in seconds (aipaas only)", (v) => parseInt(v, 10)).action(async (name, opts) => {
      try {
        const provider = getProviderFromContext(getOpts());
        const ctx = resolveContext(getOpts());
        const config = await loadConfig();
        const env2 = { ...config.env };
        if (opts.env) {
          for (const e of opts.env) {
            const idx = e.indexOf("=");
            if (idx > 0) env2[e.slice(0, idx)] = e.slice(idx + 1);
          }
        }
        const instance = await provider.create({
          name,
          port: opts.port,
          image: opts.image,
          cpus: opts.cpus,
          memory: opts.memory,
          shmSize: opts.shmSize,
          env: Object.keys(env2).length > 0 ? env2 : void 0,
          volumes: opts.volume,
          ttl: opts.ttl
        });
        console.log(source_default.green(`Sandbox '${instance.name}' created (${ctx.provider})`));
        console.log(source_default.dim(`  ID:     ${instance.id}`));
        console.log(source_default.dim(`  Status: ${instance.status}`));
        console.log(source_default.dim(`  API:    ${instance.apiUrl}`));
      } catch (err) {
        console.error(source_default.red(`Failed to create sandbox: ${err instanceof Error ? err.message : err}`));
        process.exit(1);
      }
    });
    sandbox.command("list").alias("ls").description("List sandbox containers").action(async () => {
      try {
        const provider = getProviderFromContext(getOpts());
        const instances = await provider.list();
        if (instances.length === 0) {
          console.log("No sandboxes running");
          return;
        }
        const format = detectFormat(getOpts().output);
        if (format === "json") {
          console.log(JSON.stringify(instances, null, 2));
          return;
        }
        const nameWidth = Math.max(20, ...instances.map((i) => i.name.length + 2));
        console.log(source_default.bold("NAME".padEnd(nameWidth) + "STATUS".padEnd(12) + "PORT".padEnd(8) + "IMAGE"));
        for (const inst of instances) {
          const statusColor = inst.status === "running" || inst.status === "Ready" ? source_default.green : source_default.yellow;
          console.log(
            inst.name.padEnd(nameWidth) + statusColor(inst.status.padEnd(12)) + String(inst.port).padEnd(8) + inst.image
          );
        }
      } catch (err) {
        console.error(source_default.red(`Failed to list sandboxes: ${err instanceof Error ? err.message : err}`));
        process.exit(1);
      }
    });
    sandbox.command("stop").description("Stop a running sandbox").argument("<name>", "Sandbox name or session ID").action(async (name) => {
      try {
        const provider = getProviderFromContext(getOpts());
        await provider.stop(name);
        console.log(source_default.green(`Sandbox '${name}' stopped`));
      } catch (err) {
        console.error(source_default.red(`Failed to stop sandbox: ${err instanceof Error ? err.message : err}`));
        process.exit(1);
      }
    });
    sandbox.command("rm").description("Remove a sandbox container or session").argument("<name>", "Sandbox name or session ID").option("--force", "Force remove (even if running)", false).action(async (name, opts) => {
      try {
        const provider = getProviderFromContext(getOpts());
        await provider.remove(name, opts.force);
        console.log(source_default.green(`Sandbox '${name}' removed`));
      } catch (err) {
        console.error(source_default.red(`Failed to remove sandbox: ${err instanceof Error ? err.message : err}`));
        process.exit(1);
      }
    });
    sandbox.command("logs").description("View sandbox container logs").argument("<name>", "Sandbox name or session ID").option("--tail <lines>", "Number of lines to show", parseInt).option("--follow", "Follow log output", false).action(async (name, opts) => {
      try {
        const provider = getProviderFromContext(getOpts());
        const output = await provider.logs(name, {
          tail: opts.tail,
          follow: opts.follow
        });
        process.stdout.write(output);
        if (output && !output.endsWith("\n")) process.stdout.write("\n");
      } catch (err) {
        console.error(source_default.red(`Failed to get logs: ${err instanceof Error ? err.message : err}`));
        process.exit(1);
      }
    });
    sandbox.command("status").description("Show detailed status of a sandbox").argument("<name>", "Sandbox name or session ID").action(async (name) => {
      try {
        const provider = getProviderFromContext(getOpts());
        const instance = await provider.get(name);
        if (!instance) {
          console.error(source_default.red(`Sandbox '${name}' not found`));
          process.exit(1);
          return;
        }
        console.log(source_default.bold(`Sandbox: ${instance.name}`));
        console.log(`  ID:      ${instance.id.slice(0, 12)}`);
        console.log(`  Status:  ${instance.status}`);
        console.log(`  Port:    ${instance.port}`);
        console.log(`  Image:   ${instance.image}`);
        console.log(`  Created: ${instance.createdAt}`);
        console.log(`  API:     ${instance.apiUrl}`);
        if (instance.status === "running") {
          try {
            const resp = await fetch(`${instance.apiUrl}/health`, { signal: AbortSignal.timeout(3e3) });
            if (resp.ok) {
              console.log(source_default.green(`  Health:  OK`));
            } else {
              console.log(source_default.yellow(`  Health:  HTTP ${resp.status}`));
            }
          } catch {
            console.log(source_default.yellow(`  Health:  unreachable`));
          }
        }
      } catch (err) {
        console.error(source_default.red(`Failed to get status: ${err instanceof Error ? err.message : err}`));
        process.exit(1);
      }
    });
  }
  return sandbox;
}

