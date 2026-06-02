import { cosmiconfig } from "cosmiconfig";
export var DEFAULT_API_URL = "http://127.0.0.1:8080";
export var cachedConfig = null;
export async function loadConfig() {
  if (cachedConfig) return cachedConfig;
  const explorer = cosmiconfig("aio");
  const result = await explorer.search();
  cachedConfig = {
    apiUrl: DEFAULT_API_URL,
    ...result?.config
  };
  return cachedConfig;
}
export async function resolveApiUrl(flagValue) {
  if (flagValue) return flagValue.replace(/\/+$/, "");
  if (process.env.AIO_BASE_URL) return process.env.AIO_BASE_URL.replace(/\/+$/, "");
  const config = await loadConfig();
  return config.apiUrl.replace(/\/+$/, "");
}

