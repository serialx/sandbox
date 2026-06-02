// [recovered from /usr/local/bin/aio — esbuild bundle; transpiled JS, not original TS]
// src/commands/browser.ts
import { writeFile as writeFile2 } from "node:fs/promises";
function createBrowserCommand(getClient2, getOpts) {
  const browser = new Command("browser").description("Playwright SDK browser operations");
  browser.command("restart").description("Restart the browser").option("--mode <mode>", "Restart mode: soft or hard", "hard").action(async (opts) => {
    const resp = await getClient2().post("/v1/browser/restart", {
      mode: opts.mode
    });
    printResponse(resp, detectFormat(getOpts().output));
  });
  browser.command("navigate").description("Navigate to a URL").argument("<url>", "URL to navigate to").option("--wait-until <event>", "Wait until: load, domcontentloaded, networkidle, commit", "load").option("--timeout <seconds>", "Navigation timeout", parseFloat, 30).action(async (url, opts) => {
    const resp = await getClient2().post("/v1/browser/page/navigate", {
      url,
      wait_until: opts.waitUntil,
      timeout: opts.timeout
    });
    printResponse(resp, detectFormat(getOpts().output));
  });
  browser.command("screenshot").description("Take a page screenshot").option("--full", "Capture full page", false).option("-o, --output <file>", "Save to file (default: page-screenshot.png)").option("--format <fmt>", "Image format: png or jpeg", "png").action(async (opts) => {
    const params = {};
    if (opts.full) params.full_page = "true";
    if (opts.format) params.format = opts.format;
    const outFile = opts.output || `page-screenshot.${opts.format || "png"}`;
    const { buffer } = await getClient2().getBinary("/v1/browser/page/screenshot", params);
    await writeFile2(outFile, buffer);
    console.log(`Screenshot saved to ${outFile}`);
  });
  browser.command("click").description("Click an element").argument("<selector>", "CSS selector").option("--index <n>", "Element index if multiple matches", parseInt).option("--button <btn>", "Mouse button: left, right, middle", "left").action(async (selector, opts) => {
    const body = { selector, button: opts.button };
    if (opts.index !== void 0) body.index = opts.index;
    const resp = await getClient2().post("/v1/browser/page/click", body);
    printResponse(resp, detectFormat(getOpts().output));
  });
  browser.command("fill").description("Fill an input field").argument("<text>", "Text to fill").option("-s, --selector <sel>", "CSS selector for the input").option("--index <n>", "Element index if multiple matches", parseInt).action(async (text, opts) => {
    const body = { text };
    if (opts.selector) body.selector = opts.selector;
    if (opts.index !== void 0) body.index = opts.index;
    const resp = await getClient2().post("/v1/browser/page/fill", body);
    printResponse(resp, detectFormat(getOpts().output));
  });
  browser.command("type").description("Type text character by character").argument("<text>", "Text to type").option("--delay <ms>", "Delay between keystrokes in ms", parseFloat, 0).action(async (text, opts) => {
    const resp = await getClient2().post("/v1/browser/page/type", {
      text,
      delay: opts.delay
    });
    printResponse(resp, detectFormat(getOpts().output));
  });
  browser.command("press").description("Press a key").argument("<key>", "Key to press").action(async (key) => {
    const resp = await getClient2().post("/v1/browser/page/press_key", {
      key
    });
    printResponse(resp, detectFormat(getOpts().output));
  });
  browser.command("hotkey").description("Press a key combination").argument("<keys...>", "Keys to press together").action(async (keys) => {
    const resp = await getClient2().post("/v1/browser/page/hot_key", {
      keys
    });
    printResponse(resp, detectFormat(getOpts().output));
  });
  browser.command("scroll").description("Scroll the page").option("--dir <direction>", "Scroll direction: up or down", "down").option("--amt <pixels>", "Scroll amount in pixels", parseInt, 300).action(async (opts) => {
    const resp = await getClient2().post("/v1/browser/page/scroll", {
      direction: opts.dir,
      amount: opts.amt
    });
    printResponse(resp, detectFormat(getOpts().output));
  });
  browser.command("html").description("Get page HTML").option("--outer", "Get outer HTML", false).action(async (opts) => {
    const params = {};
    if (opts.outer) params.outer = "true";
    const resp = await getClient2().get("/v1/browser/page/html", params);
    printResponse(resp, detectFormat(getOpts().output));
  });
  browser.command("text").description("Get page text content").action(async () => {
    const resp = await getClient2().get("/v1/browser/page/text");
    printResponse(resp, detectFormat(getOpts().output));
  });
  browser.command("markdown").description("Get page content as markdown").action(async () => {
    const resp = await getClient2().get("/v1/browser/page/markdown");
    printResponse(resp, detectFormat(getOpts().output));
  });
  browser.command("evaluate").description("Execute JavaScript in the page").argument("<js>", "JavaScript expression to evaluate").action(async (js) => {
    const resp = await getClient2().post("/v1/browser/page/evaluate", {
      expression: js
    });
    printResponse(resp, detectFormat(getOpts().output));
  });
  browser.command("console").description("Get console logs").option("--clear", "Clear console after reading", false).action(async (opts) => {
    const params = {};
    if (opts.clear) params.clear = "true";
    const resp = await getClient2().get("/v1/browser/page/console", params);
    printResponse(resp, detectFormat(getOpts().output));
  });
  browser.command("tabs").description("List open tabs").action(async () => {
    const resp = await getClient2().get("/v1/browser/tabs");
    printResponse(resp, detectFormat(getOpts().output));
  });
  browser.command("tab-new").description("Open a new tab").argument("[url]", "URL to open in new tab").action(async (url) => {
    const body = {};
    if (url) body.url = url;
    const resp = await getClient2().post("/v1/browser/tabs", body);
    printResponse(resp, detectFormat(getOpts().output));
  });
  browser.command("tab-close").description("Close a tab by index").argument("<index>", "Tab index to close", parseInt).action(async (index) => {
    const resp = await getClient2().delete(`/v1/browser/tabs/${index}`);
    printResponse(resp, detectFormat(getOpts().output));
  });
  browser.command("snapshot").description("Get the accessibility tree (page elements snapshot)").action(async () => {
    const resp = await getClient2().get("/v1/browser/page/elements");
    const format = detectFormat(getOpts().output);
    if (format !== "json" && resp.success && Array.isArray(resp.data)) {
      const elements = resp.data;
      if (elements.length === 0) {
        console.log("No interactive elements found");
        return;
      }
      for (const el of elements) {
        const parts = [`[${el.index}]`, `<${el.tag}>`];
        if (el.role) parts.push(`role=${el.role}`);
        if (el.text) parts.push(`"${el.text}"`);
        if (el.placeholder) parts.push(`placeholder="${el.placeholder}"`);
        if (el.href) parts.push(`href=${el.href}`);
        if (el.type) parts.push(`type=${el.type}`);
        if (!el.is_visible) parts.push("hidden");
        if (!el.is_enabled) parts.push("disabled");
        const bb = el.bounding_box;
        if (bb) parts.push(`@(${bb.x},${bb.y} ${bb.width}x${bb.height})`);
        console.log(parts.join(" "));
      }
      return;
    }
    printResponse(resp, format);
  });
  browser.command("wait").description("Wait for a condition").argument("<type>", "Wait type: selector, load, url, network_idle, timeout, etc.").option("--selector <sel>", "CSS selector (for type=selector)").option("--state <state>", "Element state (for type=selector)").option("--url <url>", "URL pattern (for type=url)").option("--timeout <seconds>", "Timeout in seconds", parseFloat, 30).option("--expression <js>", "JS expression (for type=function)").action(async (type, opts) => {
    const body = {
      type,
      timeout: opts.timeout
    };
    if (opts.selector) body.selector = opts.selector;
    if (opts.state) body.state = opts.state;
    if (opts.url) body.url = opts.url;
    if (opts.expression) body.expression = opts.expression;
    const resp = await getClient2().post("/v1/browser/page/wait", body);
    printResponse(resp, detectFormat(getOpts().output));
  });
  browser.command("cookies").description("Get browser cookies").option("--urls <urls>", "Filter by URLs (comma-separated)").action(async (opts) => {
    const params = {};
    if (opts.urls) params.urls = opts.urls;
    const resp = await getClient2().get("/v1/browser/cookies", params);
    const format = detectFormat(getOpts().output);
    if (format !== "json" && resp.success && Array.isArray(resp.data)) {
      const cookies = resp.data;
      if (cookies.length === 0) {
        console.log("No cookies found");
        return;
      }
      for (const c of cookies) {
        const parts = [`${c.name}=${c.value}`];
        if (c.domain) parts.push(`domain=${c.domain}`);
        if (c.path) parts.push(`path=${c.path}`);
        if (c.httpOnly) parts.push("httpOnly");
        if (c.secure) parts.push("secure");
        if (c.sameSite) parts.push(`sameSite=${c.sameSite}`);
        if (c.expires && c.expires > 0) {
          parts.push(`expires=${new Date(c.expires * 1e3).toISOString()}`);
        }
        console.log(parts.join("  "));
      }
      return;
    }
    printResponse(resp, format);
  });
  browser.command("cookies-set").description("Set browser cookies").option("--name <name>", "Cookie name").option("--value <value>", "Cookie value").option("--domain <domain>", "Cookie domain (e.g. .example.com)").option("--path <path>", "Cookie path").option("--http-only", "Set httpOnly flag").option("--secure", "Set secure flag").option("--same-site <policy>", "SameSite policy: Strict, Lax, None").option("--expires <seconds>", "Expires in N seconds from now", parseInt).option("--json <json>", "Set cookies from JSON array string").action(async (opts) => {
    let cookies;
    if (opts.json) {
      cookies = JSON.parse(opts.json);
    } else {
      if (!opts.name || !opts.value) {
        console.error("Either --name and --value, or --json is required");
        process.exit(1);
      }
      const cookie = {
        name: opts.name,
        value: opts.value
      };
      if (opts.domain) cookie.domain = opts.domain;
      if (opts.path) cookie.path = opts.path;
      if (opts.httpOnly) cookie.httpOnly = true;
      if (opts.secure) cookie.secure = true;
      if (opts.sameSite) cookie.sameSite = opts.sameSite;
      if (opts.expires) cookie.expires = Math.floor(Date.now() / 1e3) + opts.expires;
      cookies = [cookie];
    }
    const resp = await getClient2().post("/v1/browser/cookies", { cookies });
    printResponse(resp, detectFormat(getOpts().output));
  });
  browser.command("cookies-clear").description("Clear all browser cookies").action(async () => {
    const resp = await getClient2().delete("/v1/browser/cookies");
    printResponse(resp, detectFormat(getOpts().output));
  });
  return browser;
}

