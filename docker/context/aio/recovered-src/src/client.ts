// [recovered from /usr/local/bin/aio — esbuild bundle; transpiled JS, not original TS]
// src/client.ts
var AioClient = class {
  constructor(baseUrl, verbose = false, timeout = 3e4, authRegion) {
    this.baseUrl = baseUrl;
    this.verbose = verbose;
    this.timeout = timeout;
    this.authRegion = authRegion;
  }
  log(msg) {
    if (this.verbose) {
      process.stderr.write(source_default.gray(`[verbose] ${msg}
`));
    }
  }
  async request(method, path, options = {}) {
    const url = new URL(path, this.baseUrl);
    if (options.params) {
      for (const [k, v] of Object.entries(options.params)) {
        url.searchParams.set(k, v);
      }
    }
    const headers = {
      "Content-Type": "application/json",
      ...options.headers
    };
    const token = await (void 0)(void 0, this.authRegion);
    if (token && !headers["X-Jwt-Token"]) {
      headers["X-Jwt-Token"] = token;
    }
    const fetchOptions = {
      method,
      headers,
      signal: AbortSignal.timeout(this.timeout)
    };
    if (options.body !== void 0) {
      fetchOptions.body = JSON.stringify(options.body);
    }
    this.log(`${method} ${url.toString()}`);
    if (options.body) {
      this.log(`Body: ${JSON.stringify(options.body, null, 2)}`);
    }
    let resp;
    try {
      resp = await fetch(url.toString(), fetchOptions);
    } catch (err) {
      if (err instanceof DOMException && err.name === "TimeoutError") {
        throw new CliError(`Request timed out after ${this.timeout}ms`);
      }
      if (err instanceof TypeError) {
        throw new CliError(`Connection failed: ${err.message}
  URL: ${url.toString()}`);
      }
      throw err;
    }
    this.log(`Response: ${resp.status} ${resp.statusText}`);
    if (!resp.ok) {
      const text = await resp.text();
      let detail = text;
      try {
        const body = JSON.parse(text);
        detail = body.message || body.detail || text;
      } catch {
      }
      throw new CliError(`API error ${resp.status}: ${detail}`);
    }
    const json = await resp.json();
    this.log(`Data: ${JSON.stringify(json.data)?.slice(0, 200)}`);
    return json;
  }
  async get(path, params) {
    return this.request("GET", path, { params });
  }
  async post(path, body) {
    return this.request("POST", path, { body });
  }
  async delete(path, body) {
    return this.request("DELETE", path, { body });
  }
  async put(path, body) {
    return this.request("PUT", path, { body });
  }
  async getBinary(path, params) {
    const url = new URL(path, this.baseUrl);
    if (params) {
      for (const [k, v] of Object.entries(params)) {
        url.searchParams.set(k, v);
      }
    }
    this.log(`GET (binary) ${url.toString()}`);
    const headers = {};
    const token = await (void 0)(void 0, this.authRegion);
    if (token) {
      headers["X-Jwt-Token"] = token;
    }
    let resp;
    try {
      resp = await fetch(url.toString(), {
        headers,
        signal: AbortSignal.timeout(this.timeout)
      });
    } catch (err) {
      if (err instanceof DOMException && err.name === "TimeoutError") {
        throw new CliError(`Request timed out after ${this.timeout}ms`);
      }
      if (err instanceof TypeError) {
        throw new CliError(`Connection failed: ${err.message}`);
      }
      throw err;
    }
    if (!resp.ok) {
      throw new CliError(`API error ${resp.status}: ${resp.statusText}`);
    }
    const buffer = Buffer.from(await resp.arrayBuffer());
    return { buffer, headers: resp.headers };
  }
  async postMultipart(path, formData) {
    const url = new URL(path, this.baseUrl);
    this.log(`POST (multipart) ${url.toString()}`);
    const headers = {};
    const token = await (void 0)(void 0, this.authRegion);
    if (token) {
      headers["X-Jwt-Token"] = token;
    }
    let resp;
    try {
      resp = await fetch(url.toString(), {
        method: "POST",
        headers,
        body: formData,
        signal: AbortSignal.timeout(this.timeout)
      });
    } catch (err) {
      if (err instanceof DOMException && err.name === "TimeoutError") {
        throw new CliError(`Request timed out after ${this.timeout}ms`);
      }
      if (err instanceof TypeError) {
        throw new CliError(`Connection failed: ${err.message}`);
      }
      throw err;
    }
    if (!resp.ok) {
      const text = await resp.text();
      let detail = text;
      try {
        const body = JSON.parse(text);
        detail = body.message || body.detail || text;
      } catch {
      }
      throw new CliError(`API error ${resp.status}: ${detail}`);
    }
    return await resp.json();
  }
};
var CliError = class extends Error {
  constructor(message) {
    super(message);
    this.name = "CliError";
  }
};

