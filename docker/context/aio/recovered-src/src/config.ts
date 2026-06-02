// [recovered from /usr/local/bin/aio — esbuild bundle; transpiled JS, not original TS]
// src/config.ts
var import_cosmiconfig = __toESM(require_dist(), 1);
var DEFAULT_API_URL = "http://127.0.0.1:8080";
var cachedConfig = null;
async function loadConfig() {
  if (cachedConfig) return cachedConfig;
  const explorer = (0, import_cosmiconfig.cosmiconfig)("aio");
  const result = await explorer.search();
  cachedConfig = {
    apiUrl: DEFAULT_API_URL,
    ...result?.config
  };
  return cachedConfig;
}
async function resolveApiUrl(flagValue) {
  if (flagValue) return flagValue.replace(/\/+$/, "");
  if (process.env.AIO_BASE_URL) return process.env.AIO_BASE_URL.replace(/\/+$/, "");
  const config = await loadConfig();
  return config.apiUrl.replace(/\/+$/, "");
}

