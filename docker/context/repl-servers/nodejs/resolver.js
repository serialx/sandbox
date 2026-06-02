/**
 * Module Resolver
 *
 * Handles module resolution across multiple paths:
 * - Working directory (cwd)
 * - Runtime node_modules (pre-installed)
 * - Global npm node_modules
 */

import * as path from 'node:path';
import * as fs from 'node:fs';
import { createRequire } from 'node:module';
import { execSync } from 'node:child_process';

// Runtime node_modules (pre-installed packages in container)
export const RUNTIME_NODE_MODULES = process.env.RUNTIME_NODE_MODULES || '/opt/runtime/nodejs/node_modules';

// Global npm node_modules
export let GLOBAL_NPM_MODULES = process.env.GLOBAL_NPM_MODULES || null;

// Detect global npm path
if (!GLOBAL_NPM_MODULES) {
  // First, try to derive from NPM_CONFIG_PREFIX (set by run.sh or supervisord)
  const npmConfigPrefix = process.env.NPM_CONFIG_PREFIX;
  if (npmConfigPrefix) {
    const derivedPath = path.join(npmConfigPrefix, 'lib', 'node_modules');
    if (fs.existsSync(derivedPath)) {
      GLOBAL_NPM_MODULES = derivedPath;
      console.log(`Derived global npm modules from NPM_CONFIG_PREFIX: ${GLOBAL_NPM_MODULES}`);
    }
  }

  // Second, try to derive from HOME (consistent with run.sh: $HOME/.npm-global)
  if (!GLOBAL_NPM_MODULES) {
    const home = process.env.HOME;
    if (home) {
      const homeDerivedPath = path.join(home, '.npm-global', 'lib', 'node_modules');
      if (fs.existsSync(homeDerivedPath)) {
        GLOBAL_NPM_MODULES = homeDerivedPath;
        console.log(`Derived global npm modules from HOME: ${GLOBAL_NPM_MODULES}`);
      }
    }
  }

  // Third, try npm root -g
  if (!GLOBAL_NPM_MODULES) {
    try {
      const npmRoot = execSync('npm root -g', { encoding: 'utf-8' }).trim();
      if (npmRoot && fs.existsSync(npmRoot)) {
        GLOBAL_NPM_MODULES = npmRoot;
        console.log(`Detected global npm modules path: ${GLOBAL_NPM_MODULES}`);
      } else {
        throw new Error(`npm root -g returned invalid path: ${npmRoot}`);
      }
    } catch (e) {
      const fallbackPaths = ['/usr/local/lib/node_modules', '/usr/lib/node_modules'];
      for (const p of fallbackPaths) {
        if (fs.existsSync(p)) {
          GLOBAL_NPM_MODULES = p;
          console.log(`Using fallback global npm modules path: ${GLOBAL_NPM_MODULES}`);
          break;
        }
      }
      if (!GLOBAL_NPM_MODULES) {
        console.warn('No global npm modules path found, global packages may not be available');
      }
    }
  }
}

/**
 * Create a require function that searches: cwd -> runtime -> global npm
 */
export function createMultiPathRequire(contextDir) {
  const contextRequire = createRequire(path.join(contextDir, 'repl.js'));

  const fallbackRequires = [];

  // Global npm modules take priority over runtime modules
  // This allows user-installed packages to override pre-installed ones
  if (GLOBAL_NPM_MODULES && fs.existsSync(GLOBAL_NPM_MODULES)) {
    fallbackRequires.push({
      name: 'global-npm',
      require: createRequire(path.join(GLOBAL_NPM_MODULES, '_repl.js'))
    });
  }

  if (RUNTIME_NODE_MODULES && fs.existsSync(RUNTIME_NODE_MODULES)) {
    fallbackRequires.push({
      name: 'runtime',
      require: createRequire(path.join(RUNTIME_NODE_MODULES, '_repl.js'))
    });
  }

  const multiRequire = (id) => {
    if (id.startsWith('./') || id.startsWith('../') || path.isAbsolute(id)) {
      return contextRequire(id);
    }

    try {
      return contextRequire(id);
    } catch (e) {
      if (e.code === 'MODULE_NOT_FOUND') {
        for (const fallback of fallbackRequires) {
          try {
            return fallback.require(id);
          } catch (e2) {
            // Continue to next fallback
          }
        }
      }
      throw e;
    }
  };

  multiRequire.resolve = (id, options) => {
    if (id.startsWith('./') || id.startsWith('../') || path.isAbsolute(id)) {
      return contextRequire.resolve(id, options);
    }
    try {
      return contextRequire.resolve(id, options);
    } catch (e) {
      if (e.code === 'MODULE_NOT_FOUND') {
        for (const fallback of fallbackRequires) {
          try {
            return fallback.require.resolve(id, options);
          } catch (e2) {
            // Continue to next fallback
          }
        }
      }
      throw e;
    }
  };

  multiRequire.cache = contextRequire.cache;
  multiRequire.main = contextRequire.main;

  return multiRequire;
}
