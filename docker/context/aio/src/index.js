import { Command } from "commander";
import source_default from "chalk";
import { AioClient, CliError } from "./client.js";
import { createBrowserCommand } from "./commands/browser.js";
import { createGuiCommand } from "./commands/gui.js";
import { createMcpCommand } from "./commands/mcp.js";
import { createSandboxCommand } from "./commands/sandbox.js";
import { createSkillsCommand } from "./commands/skills.js";
import { loadConfig, resolveApiUrl } from "./config.js";
import { loadContext, resolveContext } from "./context.js";
import { printError } from "./output.js";
export var program2 = new Command();
program2.name("aio").description("CLI for AIO Sandbox \u2014 browser, GUI, shell, file, skills, and MCP operations").version("0.3.12").addHelpText("after", () => {
  if (in_sandbox) return "";
  const ctx = loadContext();
  const target = ctx.provider === "aipaas" ? `aipaas (${ctx.psm ?? "no psm"}, ${ctx.region ?? "CN"})` : ctx.provider === "boxlite" ? `boxlite (${ctx.apiUrl ?? "http://127.0.0.1:8080"})` : `local (${ctx.apiUrl ?? "http://127.0.0.1:8080"})`;
  return `
${source_default.dim(`Current context: ${target}`)}
${source_default.dim("Switch with: aio use <psm> | aio use --local | aio use --boxlite")}`;
}).option("--api-url <url>", "Override API base URL").option("--provider <type>", "Override provider (local | aipaas | boxlite)").option("--psm <psm>", "Override PSM identifier").option("--region <region>", "Override region (CN, CN_EAST, CN_NORTH, CN_NORTH6, BOE, I18N, I18N_OFFICE, I18N_PROD, I18N_BD, BOE_I18N, CHINASINF_NORTH, BOESINF_NORTH, ASIA_CIS, SANDBOX, BOE_SANDBOX, LOCAL)").option("--session <id>", "Override session ID").option("--output <format>", "Output format: json, table, text (default: auto-detect)").option("-v, --verbose", "Show request/response details").option("-q, --quiet", "Suppress non-essential output");
export function getGlobalOpts() {
  return program2.opts();
}
export var clientInstance = null;
export function getClient() {
  if (clientInstance) return clientInstance;
  throw new Error("Client not initialized");
}
var { in_sandbox } = await loadConfig();
// NOTE: the upstream build dead-code-eliminated the non-sandbox commands
// (shell/tty/file/install/login/logout/update/web/use) and the aipaas remote
// provider behind `if (false)` / `void 0`; only the in-sandbox command set
// below survives in this build, so those blocks are dropped here.
program2.addCommand(createGuiCommand(getClient, getGlobalOpts));
program2.addCommand(createBrowserCommand(getClient, getGlobalOpts));
program2.addCommand(createSandboxCommand(getClient, getGlobalOpts, in_sandbox ?? false));
program2.addCommand(createMcpCommand(getClient, getGlobalOpts));
program2.addCommand(createSkillsCommand(getClient, getGlobalOpts));
export var SKIP_CLIENT_COMMANDS = /* @__PURE__ */ new Set(["use", "login", "logout", "update"]);
export var SANDBOX_MGMT_COMMANDS = /* @__PURE__ */ new Set(["create", "list", "ls", "stop", "rm", "remove", "logs", "status"]);
program2.hook("preAction", async (_thisCommand, actionCommand) => {
  const cmdName = actionCommand.name();
  const parentName = actionCommand.parent?.name();
  if (SKIP_CLIENT_COMMANDS.has(cmdName)) return;
  if (parentName === "sandbox" && SANDBOX_MGMT_COMMANDS.has(cmdName)) return;
  const flags = program2.opts();
  const ctx = resolveContext(flags);
  let apiUrl;
  if (ctx.provider === "aipaas") {
    if (!void 0) {
      throw new CliError("Remote provider is not available in this build. Use --provider local.");
    }
    if (!ctx.psm) {
      throw new CliError("PSM is required for aipaas provider. Run: aio use <psm>");
    }
    const provider = new (void 0)({
      psm: ctx.psm,
      region: ctx.region?.toUpperCase() ?? "CN"
    });
    if (ctx.session) {
      const instance = await provider.get(ctx.session);
      if (!instance) throw new CliError(`Session '${ctx.session}' not found`);
      apiUrl = instance.apiUrl;
    } else {
      const instances = await provider.list();
      if (instances.length === 0) throw new CliError("No active aipaas sessions. Create one with: aio sandbox create");
      const latest = instances.sort(
        (a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime()
      )[0];
      apiUrl = latest.apiUrl;
    }
  } else {
    apiUrl = await resolveApiUrl(ctx.apiUrl);
  }
  clientInstance = new AioClient(
    apiUrl,
    flags.verbose !== void 0,
    void 0,
    ctx.provider === "aipaas" ? (void 0)(ctx.region) : void 0
  );
});
export async function main() {
  try {
    await program2.parseAsync(process.argv);
  } catch (err) {
    if (err instanceof CliError) {
      printError(err);
      process.exit(1);
    }
    throw err;
  }
}
main().catch((err) => {
  printError(err);
  process.exit(1);
});
