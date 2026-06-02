// [recovered from /usr/local/bin/aio — esbuild bundle; transpiled JS, not original TS]
// src/commands/skills.ts
import { basename as basename2 } from "node:path";
import { readFile } from "node:fs/promises";
function printSkillsList(resp) {
  const skills = resp.data?.skills ?? [];
  if (skills.length === 0) {
    console.log("No skills registered.");
    return;
  }
  for (const skill of skills) {
    const description = typeof skill.metadata?.description === "string" ? ` - ${skill.metadata.description}` : "";
    console.log(`${skill.name}: ${skill.path}${description}`);
  }
}
function printSkillContent(resp) {
  const content = resp.data?.content;
  if (typeof content === "string") {
    console.log(content);
    return;
  }
  printResponse(resp, "text");
}
function createSkillsCommand(getClient2, getOpts) {
  const skills = new Command("skills").description("Manage skills");
  skills.command("register").description("Register skills from a sandbox directory or a local zip").option("--path <path>", "Path to a skills directory in sandbox").option("--zip <file>", "Local zip archive containing skill directories").option("--to <path>", "Destination directory in sandbox for zip upload").option("--name <name>", "Optional directory name override for zip extraction").action(async (opts) => {
    const hasPath = typeof opts.path === "string";
    const hasZip = typeof opts.zip === "string";
    if (hasPath === hasZip) {
      throw new CliError("Use either --path or --zip (and not both)");
    }
    if (hasPath) {
      const resp2 = await getClient2().post("/v1/skills/register", { path: opts.path });
      printResponse(resp2, detectFormat(getOpts().output));
      return;
    }
    if (!opts.to) {
      throw new CliError("--to is required when using --zip");
    }
    const zipData = await readFile(opts.zip);
    const blob = new Blob([zipData]);
    const formData = new FormData();
    formData.set("file", blob, basename2(opts.zip));
    formData.set("path", opts.to);
    if (opts.name) {
      formData.set("name", opts.name);
    }
    const resp = await getClient2().postMultipart("/v1/skills/register", formData);
    printResponse(resp, detectFormat(getOpts().output));
  });
  skills.command("list").description("List registered skills").option("--names <names...>", "Only list selected skill names").action(async (opts) => {
    const params = {};
    if (Array.isArray(opts.names) && opts.names.length > 0) {
      params.names = opts.names.join(",");
    }
    const resp = await getClient2().get(
      "/v1/skills/metadatas",
      Object.keys(params).length > 0 ? params : void 0
    );
    const format = detectFormat(getOpts().output);
    if (format === "json") {
      printResponse(resp, format);
      return;
    }
    printSkillsList(resp);
  });
  skills.command("load").alias("content").description("Load skill content by name").argument("<name>", "Skill name").action(async (name) => {
    const resp = await getClient2().get(
      `/v1/skills/${encodeURIComponent(name)}/content`
    );
    const format = detectFormat(getOpts().output);
    if (format === "json") {
      printResponse(resp, format);
      return;
    }
    printSkillContent(resp);
  });
  skills.command("delete").description("Delete a registered skill by name").argument("<name>", "Skill name").action(async (name) => {
    const resp = await getClient2().delete(`/v1/skills/${encodeURIComponent(name)}`);
    printResponse(resp, detectFormat(getOpts().output));
  });
  return skills;
}

