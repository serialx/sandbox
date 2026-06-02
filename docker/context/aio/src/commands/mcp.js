import { Command } from "commander";
import { detectFormat, printResponse } from "../output.js";
export function printToolsMarkdown(tools) {
  if (tools.length === 0) {
    console.log("No tools available.");
    return;
  }
  console.log(`${tools.length} tools available:
`);
  for (const tool of tools) {
    console.log(`## ${tool.name}`);
    if (tool.description) {
      console.log(tool.description);
    }
    const schema = tool.inputSchema;
    if (schema?.properties && Object.keys(schema.properties).length > 0) {
      const required = new Set(schema.required || []);
      console.log("\nParameters:");
      for (const [name, prop] of Object.entries(schema.properties)) {
        const parts = [];
        if (prop.type) parts.push(prop.type);
        if (required.has(name)) parts.push("required");
        if (prop.enum) parts.push(`one of: ${prop.enum.join(", ")}`);
        const meta = parts.length > 0 ? ` (${parts.join(", ")})` : "";
        const desc = prop.description ? ` \u2014 ${prop.description}` : "";
        console.log(`- \`${name}\`${meta}${desc}`);
      }
    }
    console.log("");
  }
}
export function createMcpCommand(getClient2, getOpts) {
  const mcp = new Command("mcp").description("MCP server management and tool execution");
  mcp.command("list").description("List configured MCP servers").option("--all", "Include hidden MCP servers").action(async (opts) => {
    const resp = await getClient2().get(
      "/v1/mcp/servers",
      opts.all ? { include_hidden: "true" } : void 0
    );
    const format = detectFormat(getOpts().output);
    if (format === "json") {
      printResponse(resp, format);
      return;
    }
    const servers = resp.data;
    if (!servers || servers.length === 0) {
      console.log("No MCP servers configured.");
      return;
    }
    for (const s of servers) {
      console.log(s);
    }
  });
  mcp.command("tools").description("List available tools from an MCP server").argument("<server>", "MCP server name").action(async (server2) => {
    const resp = await getClient2().get(`/v1/mcp/${encodeURIComponent(server2)}/tools`);
    const format = detectFormat(getOpts().output);
    if (format === "json") {
      printResponse(resp, format);
      return;
    }
    const data = resp.data;
    const tools = data?.tools || [];
    printToolsMarkdown(tools);
  });
  mcp.command("call").description("Execute a tool on an MCP server").argument("<server>", "MCP server name").argument("<tool>", "Tool name to execute").option("--args <json>", "Tool arguments as JSON string", "{}").action(async (server2, tool, opts) => {
    let args = {};
    try {
      args = JSON.parse(opts.args);
    } catch {
      console.error("Error: --args must be valid JSON");
      process.exit(1);
    }
    const resp = await getClient2().post(
      `/v1/mcp/${encodeURIComponent(server2)}/tools/${encodeURIComponent(tool)}`,
      args
    );
    const format = detectFormat(getOpts().output);
    if (format === "json") {
      printResponse(resp, format);
      return;
    }
    const data = resp.data;
    if (data?.content) {
      for (const item of data.content) {
        if (item.text) console.log(item.text);
      }
    } else {
      printResponse(resp, format);
    }
  });
  return mcp;
}

