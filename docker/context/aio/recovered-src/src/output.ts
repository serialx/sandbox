// [recovered from /usr/local/bin/aio — esbuild bundle; transpiled JS, not original TS]
// src/output.ts
function detectFormat(override) {
  if (override === "json" || override === "table" || override === "text") {
    return override;
  }
  return process.stdout.isTTY ? "text" : "text";
}
function printResponse(resp, format) {
  if (format === "json") {
    console.log(JSON.stringify(resp.data ?? resp, null, 2));
    return;
  }
  if (!resp.success) {
    console.error(source_default.red(`Error: ${resp.message}`));
    if (resp.hint) console.error(source_default.yellow(`Hint: ${resp.hint}`));
    return;
  }
  const { data } = resp;
  if (data === null || data === void 0) {
    console.log(source_default.green(resp.message || "OK"));
    return;
  }
  if (typeof data === "string") {
    console.log(data);
    return;
  }
  if (Array.isArray(data)) {
    if (data.length === 0) {
      console.log(source_default.dim("(empty)"));
      return;
    }
    if (typeof data[0] === "object") {
      printArrayPlain(data);
    } else {
      for (const item of data) {
        console.log(String(item));
      }
    }
    return;
  }
  if (typeof data === "object") {
    printKVPlain(data);
    return;
  }
  console.log(String(data));
}
function printArrayPlain(rows) {
  for (let i = 0; i < rows.length; i++) {
    if (i > 0) console.log("");
    printKVPlain(rows[i]);
  }
}
function printKVPlain(obj) {
  for (const [k, v] of Object.entries(obj)) {
    console.log(`${k}: ${formatValue(v)}`);
  }
}
function formatValue(v) {
  if (v === null || v === void 0) return "null";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}
function printError(err) {
  if (err instanceof Error) {
    console.error(source_default.red(`Error: ${err.message}`));
  } else {
    console.error(source_default.red(`Error: ${err}`));
  }
}

