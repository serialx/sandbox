/**
 * Worker Thread for Node.js REPL Execution
 *
 * Runs code in an isolated Worker Thread with proper timeout handling.
 * Uses vm.Script for code execution in a persistent context.
 *
 * Message Protocol:
 *
 * Main → Worker (execute):
 *   { type: 'execute', code: string, timeout: number }
 *
 * Main → Worker (init):
 *   { type: 'init', cwd: string, sessionId: string }
 *
 * Worker → Main (response):
 *   {
 *     success: boolean,
 *     stdout: string,
 *     stderr: string,
 *     result: string,
 *     error: { name, message, stack } | null
 *   }
 */

import { parentPort, workerData } from 'node:worker_threads';
import * as vm from 'node:vm';
import * as util from 'node:util';
import swc from '@swc/core';
import { createMultiPathRequire } from './resolver.js';

/**
 * Global handlers for uncaught errors to prevent Worker from crashing
 * These handle errors that escape the executeCode try/catch (e.g., delayed Promise rejections)
 */
process.on('unhandledRejection', (reason, promise) => {
  console.error(`[Worker] Unhandled Promise rejection:`, reason);
  // Don't exit - keep the worker alive
});

process.on('uncaughtException', (err) => {
  console.error(`[Worker] Uncaught exception:`, err);
  // Don't exit - keep the worker alive
  // Note: This may leave the worker in an inconsistent state,
  // but session-manager will handle timeouts and restart if needed
});

// Worker state
let context = null;
let sessionId = null;
let cwd = '/tmp';
let stdout = [];
let stderr = [];

/**
 * Initialize from workerData if provided
 */
if (workerData) {
  sessionId = workerData.sessionId;
  cwd = workerData.cwd || '/tmp';
  initContext();
}

/**
 * Create a safe process proxy that blocks dangerous methods
 * but exposes safe properties including env
 */
function createSafeProcess() {
  // Methods that can kill/crash the process - block these
  const blockedMethods = new Set([
    'exit',
    'abort',
    'kill',
    'reallyExit',
    '_kill',
    'dlopen',        // Can load native modules to bypass sandbox
    'binding',       // Internal bindings
    '_linkedBinding',
  ]);

  // Create a proxy to intercept property access
  return new Proxy(process, {
    get(target, prop) {
      if (blockedMethods.has(prop)) {
        return () => {
          throw new Error(`process.${prop}() is not allowed in sandbox`);
        };
      }

      const value = target[prop];

      // Bind functions to original process object
      if (typeof value === 'function') {
        return value.bind(target);
      }

      return value;
    },
    set(target, prop, value) {
      // Allow setting properties (some code may do process.env.FOO = 'bar')
      target[prop] = value;
      return true;
    },
  });
}

/**
 * Initialize the VM context
 */
function initContext() {
  const customRequire = createMultiPathRequire(cwd);
  const safeProcess = createSafeProcess();

  // Create a new VM context
  context = vm.createContext({
    // Node.js globals
    global: undefined,
    globalThis: undefined,
    process: safeProcess,  // Use safe process proxy
    Buffer,
    URL,
    URLSearchParams,
    TextEncoder,
    TextDecoder,

    // Timers
    setTimeout,
    setInterval,
    setImmediate,
    clearTimeout,
    clearInterval,
    clearImmediate,
    queueMicrotask,

    // Module system
    require: customRequire,
    __dirname: cwd,
    __filename: `${cwd}/repl.js`,

    // Console will be set up for each execution
    console: undefined,
  });

  // Set up globalThis reference
  context.global = context;
  context.globalThis = context;
}

/**
 * Create a console that captures output
 */
function createCaptureConsole() {
  const format = (...args) =>
    args.map((a) => (typeof a === 'string' ? a : util.inspect(a))).join(' ');

  return {
    log: (...args) => stdout.push(format(...args)),
    info: (...args) => stdout.push(format(...args)),
    debug: (...args) => stdout.push(format(...args)),
    trace: (...args) => stdout.push(format(...args)),
    error: (...args) => stderr.push(format(...args)),
    warn: (...args) => stderr.push(format(...args)),
    dir: (obj, opts) => stdout.push(util.inspect(obj, { ...opts, colors: false })),
    table: (data) => stdout.push(util.inspect(data, { colors: false })),
    time: console.time.bind(console),
    timeEnd: console.timeEnd.bind(console),
    timeLog: console.timeLog.bind(console),
    assert: console.assert.bind(console),
    clear: () => {},
    count: console.count.bind(console),
    countReset: console.countReset.bind(console),
    group: () => {},
    groupEnd: () => {},
  };
}

/**
 * Transform ESM code to CommonJS using SWC
 */
function transformCode(code) {
  const hasImport = /\bimport\s+/.test(code);
  const hasExport = /\bexport\s+/.test(code);
  const hasAwait = /\bawait\s+/.test(code);

  if (!hasImport && !hasExport && !hasAwait) {
    return code;
  }

  let output = code;

  // Transform ESM imports/exports with SWC
  if (hasImport || hasExport) {
    try {
      const result = swc.transformSync(code, {
        jsc: {
          parser: { syntax: 'ecmascript' },
          target: 'es2020',
        },
        module: {
          type: 'commonjs',
          noInterop: true,
        },
      });

      output = result.code;

      // Remove "use strict"
      output = output.replace(/^"use strict";\n?/, '');

      // Replace _xxx.default with _xxx (for default imports)
      output = output.replace(/(_\w+)\.default\b/g, '$1');

      // Collect variable mappings: _path -> path
      const importMap = new Map();
      output.replace(/const (_\w+) = require\(/g, (match, varName) => {
        importMap.set(varName, varName.slice(1));
        return match;
      });

      // Restore original variable names
      for (const [swcName, originalName] of importMap) {
        output = output.replace(new RegExp(`\\b${swcName}\\b`, 'g'), originalName);
      }
    } catch (err) {
      // If SWC fails, continue with original code
      output = code;
    }
  }

  // Wrap in async IIFE if has top-level await
  if (hasAwait) {
    // Hoist function/class declarations to globalThis
    output = output
      .replace(/^(\s*)(async\s+)?function\s+(\w+)\s*\(/gm, '$1globalThis.$3 = $2function $3(')
      .replace(/^(\s*)class\s+(\w+)/gm, '$1globalThis.$2 = class $2');

    output = `(async () => { ${output} })()`;
  }

  return output;
}

/**
 * Execute code with timeout
 *
 * @param {string} code - Code to execute
 * @param {number} timeout - Timeout in milliseconds
 * @returns {Promise<any>} - Execution result
 */
async function executeCode(code, timeout) {
  const execCode = transformCode(code);

  // Create script
  const script = new vm.Script(execCode, { filename: 'repl' });

  // Execute with vm timeout for synchronous code
  let result = script.runInContext(context, { timeout });

  // Handle async result (Promise)
  if (result && typeof result.then === 'function') {
    // Create a timeout race for async code
    const timeoutPromise = new Promise((_, reject) => {
      setTimeout(() => {
        reject(new Error(`Execution timed out after ${timeout}ms`));
      }, timeout);
    });

    result = await Promise.race([result, timeoutPromise]);
  }

  return result;
}

/**
 * Handle messages from main thread
 */
parentPort.on('message', async (message) => {
  const { type } = message;

  switch (type) {
    case 'init': {
      sessionId = message.sessionId;
      cwd = message.cwd || '/tmp';
      initContext();
      parentPort.postMessage({ type: 'init_done', success: true });
      break;
    }

    case 'execute': {
      const { code, timeout = 30000 } = message;

      // Reset output buffers
      stdout = [];
      stderr = [];

      // Set up console capture
      context.console = createCaptureConsole();

      try {
        // Execute code
        const result = await executeCode(code, timeout);

        // Format result
        let resultStr = '';
        if (result !== undefined) {
          try {
            resultStr = util.inspect(result, { depth: 3, colors: false });
          } catch (e) {
            resultStr = String(result);
          }
        }

        parentPort.postMessage({
          type: 'execute_done',
          success: true,
          stdout: stdout.join('\n'),
          stderr: stderr.join('\n'),
          result: resultStr,
          error: null,
        });
      } catch (err) {
        // Determine if it's a timeout error
        const isTimeout =
          err.message?.includes('timed out') ||
          err.message?.includes('Script execution timed out');

        // Reset context on timeout to maintain consistent behavior
        if (isTimeout) {
          initContext();
        }

        const errorMessage = isTimeout
          ? `Execution timed out after ${timeout}ms. Session state has been reset.`
          : (err.message || String(err));

        stderr.push(`${isTimeout ? 'TimeoutError' : (err.name || 'Error')}: ${errorMessage}`);

        parentPort.postMessage({
          type: 'execute_done',
          success: false,
          stdout: stdout.join('\n'),
          stderr: stderr.join('\n'),
          result: '',
          error: {
            name: isTimeout ? 'TimeoutError' : (err.name || 'Error'),
            message: errorMessage,
            stack: err.stack || '',
          },
        });
      }
      break;
    }

    case 'ping': {
      // Health check
      parentPort.postMessage({ type: 'pong' });
      break;
    }

    default: {
      parentPort.postMessage({
        type: 'error',
        error: { name: 'Error', message: `Unknown message type: ${type}` },
      });
    }
  }
});

// Signal ready
parentPort.postMessage({ type: 'ready' });
