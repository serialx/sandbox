// [recovered from /usr/local/bin/aio — esbuild bundle; transpiled JS, not original TS]
// src/providers/container.ts
var LABEL_KEY = "aio-sandbox";
var CONTAINER_PREFIX = "aio-";
var DEFAULT_IMAGE = "sandbox:latest";
var DEFAULT_PORT = 8080;
var ContainerProvider = class _ContainerProvider {
  constructor(bin) {
    this.bin = bin;
  }
  static detect() {
    const { bin } = detectContainerRuntime();
    return new _ContainerProvider(bin);
  }
  static fromBin(bin) {
    return new _ContainerProvider(bin);
  }
  exec(args) {
    return execCmd(this.bin, args);
  }
  async create(opts) {
    const name = opts.name ?? `sandbox-${Date.now()}`;
    const containerName = CONTAINER_PREFIX + name;
    const port = opts.port ?? DEFAULT_PORT;
    const image = opts.image ?? DEFAULT_IMAGE;
    const existing = await this.get(name);
    if (existing) {
      throw new Error(`Sandbox '${name}' already exists (status: ${existing.status})`);
    }
    const args = [
      "run",
      "-d",
      "--name",
      containerName,
      "--label",
      `${LABEL_KEY}=true`,
      "--label",
      `${LABEL_KEY}-name=${name}`,
      "-p",
      `${port}:8080`,
      "--security-opt",
      "seccomp=unconfined",
      "--shm-size",
      opts.shmSize ?? "4g"
    ];
    if (opts.cpus) args.push("--cpus", opts.cpus);
    if (opts.memory) args.push("--memory", opts.memory);
    if (opts.env) {
      for (const [k, v] of Object.entries(opts.env)) {
        args.push("-e", `${k}=${v}`);
      }
    }
    if (opts.volumes) {
      for (const v of opts.volumes) {
        args.push("-v", v);
      }
    }
    args.push(image);
    const containerId = this.exec(args);
    return {
      name,
      id: containerId.slice(0, 12),
      status: "running",
      port,
      image,
      createdAt: (/* @__PURE__ */ new Date()).toISOString(),
      apiUrl: `http://127.0.0.1:${port}`
    };
  }
  async list() {
    let raw;
    try {
      raw = this.exec([
        "ps",
        "-a",
        "--filter",
        `label=${LABEL_KEY}=true`,
        "--format",
        "{{json .}}"
      ]);
    } catch {
      return [];
    }
    if (!raw) return [];
    return raw.split("\n").filter(Boolean).map((line) => {
      const c = JSON.parse(line);
      const cName = (c.Names ?? c.Name ?? "").replace(new RegExp(`^${CONTAINER_PREFIX}`), "");
      const portStr = c.Ports ?? "";
      const portMatch = portStr.match(/0\.0\.0\.0:(\d+)/);
      const port = portMatch ? parseInt(portMatch[1]) : 0;
      return {
        name: cName,
        id: (c.ID ?? "").slice(0, 12),
        status: c.Status ?? c.State ?? "unknown",
        port,
        image: c.Image ?? "",
        createdAt: c.CreatedAt ?? "",
        apiUrl: port ? `http://127.0.0.1:${port}` : ""
      };
    });
  }
  async get(name) {
    const all = await this.list();
    return all.find((s) => s.name === name) ?? null;
  }
  async stop(name) {
    this.exec(["stop", CONTAINER_PREFIX + name]);
  }
  async remove(name, force = false) {
    const args = ["rm"];
    if (force) args.push("-f");
    args.push(CONTAINER_PREFIX + name);
    this.exec(args);
  }
  async logs(name, opts) {
    const args = ["logs"];
    if (opts?.tail) args.push("--tail", String(opts.tail));
    if (opts?.follow) args.push("-f");
    args.push(CONTAINER_PREFIX + name);
    return this.exec(args);
  }
};

