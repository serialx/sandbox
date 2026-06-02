import { Command } from "commander";
import { detectFormat, printResponse } from "../output.js";
import { writeFile } from "node:fs/promises";
export function createGuiCommand(getClient2, getOpts) {
  const gui = new Command("gui").description("GUI-level actions (pyautogui / low-level mouse & keyboard)");
  gui.command("screenshot").description("Take a screenshot of the desktop").option("-o, --output <file>", "Save PNG to file (default: screenshot.png)").action(async (opts) => {
    const client = getClient2();
    const outFile = opts.output || "screenshot.png";
    const { buffer } = await client.getBinary("/v1/browser/screenshot");
    await writeFile(outFile, buffer);
    console.log(`Screenshot saved to ${outFile}`);
  });
  gui.command("tap").description("Click at coordinates").argument("<x>", "X coordinate", parseFloat).argument("<y>", "Y coordinate", parseFloat).action(async (x, y) => {
    const resp = await getClient2().post("/v1/browser/actions", {
      action_type: "CLICK",
      x,
      y
    });
    printResponse(resp, detectFormat(getOpts().output));
  });
  gui.command("type").description("Type text").argument("<text>", "Text to type").action(async (text) => {
    const resp = await getClient2().post("/v1/browser/actions", {
      action_type: "TYPING",
      text
    });
    printResponse(resp, detectFormat(getOpts().output));
  });
  gui.command("press").description("Press a key").argument("<key>", "Key to press").action(async (key) => {
    const resp = await getClient2().post("/v1/browser/actions", {
      action_type: "PRESS",
      key
    });
    printResponse(resp, detectFormat(getOpts().output));
  });
  gui.command("hotkey").description("Press a key combination").argument("<keys...>", "Keys to press together").action(async (keys) => {
    const resp = await getClient2().post("/v1/browser/actions", {
      action_type: "HOTKEY",
      keys
    });
    printResponse(resp, detectFormat(getOpts().output));
  });
  gui.command("scroll").description("Scroll the screen").option("--dx <n>", "Horizontal scroll amount", parseInt, 0).option("--dy <n>", "Vertical scroll amount", parseInt, 0).action(async (opts) => {
    const resp = await getClient2().post("/v1/browser/actions", {
      action_type: "SCROLL",
      dx: opts.dx,
      dy: opts.dy
    });
    printResponse(resp, detectFormat(getOpts().output));
  });
  gui.command("move").description("Move mouse to coordinates").argument("<x>", "X coordinate", parseFloat).argument("<y>", "Y coordinate", parseFloat).action(async (x, y) => {
    const resp = await getClient2().post("/v1/browser/actions", {
      action_type: "MOVE_TO",
      x,
      y
    });
    printResponse(resp, detectFormat(getOpts().output));
  });
  gui.command("drag").description("Drag to coordinates").argument("<x>", "X coordinate", parseFloat).argument("<y>", "Y coordinate", parseFloat).action(async (x, y) => {
    const resp = await getClient2().post("/v1/browser/actions", {
      action_type: "DRAG_TO",
      x,
      y
    });
    printResponse(resp, detectFormat(getOpts().output));
  });
  gui.command("double-click").description("Double-click at coordinates").argument("[x]", "X coordinate", parseFloat).argument("[y]", "Y coordinate", parseFloat).action(async (x, y) => {
    const body = { action_type: "DOUBLE_CLICK" };
    if (x !== void 0) body.x = x;
    if (y !== void 0) body.y = y;
    const resp = await getClient2().post("/v1/browser/actions", body);
    printResponse(resp, detectFormat(getOpts().output));
  });
  gui.command("right-click").description("Right-click at coordinates").argument("[x]", "X coordinate", parseFloat).argument("[y]", "Y coordinate", parseFloat).action(async (x, y) => {
    const body = { action_type: "RIGHT_CLICK" };
    if (x !== void 0) body.x = x;
    if (y !== void 0) body.y = y;
    const resp = await getClient2().post("/v1/browser/actions", body);
    printResponse(resp, detectFormat(getOpts().output));
  });
  gui.command("wait").description("Wait for a duration").argument("<seconds>", "Seconds to wait", parseFloat).action(async (duration) => {
    const resp = await getClient2().post("/v1/browser/actions", {
      action_type: "WAIT",
      duration
    });
    printResponse(resp, detectFormat(getOpts().output));
  });
  gui.command("info").description("Get browser/screen info").action(async () => {
    const resp = await getClient2().get("/v1/browser/info");
    printResponse(resp, detectFormat(getOpts().output));
  });
  return gui;
}

