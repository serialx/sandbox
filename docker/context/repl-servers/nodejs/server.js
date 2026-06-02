/**
 * Node.js REPL Server
 *
 * HTTP API for stateful JavaScript code execution using Worker Threads.
 *
 * Endpoints:
 * - POST /execute - Execute code in a session
 * - POST /sessions - Create a new session
 * - GET /sessions - List all sessions
 * - GET /sessions/:id - Get session details
 * - PATCH /sessions/:id - Update session configuration
 * - DELETE /sessions/:id - Delete a session
 * - GET /health - Health check
 */

import Fastify from 'fastify';
import { randomUUID } from 'node:crypto';
import * as fs from 'node:fs';
import * as path from 'node:path';
import {
  sessions,
  createSession,
  closeSession,
  executeInSession,
  listSessions,
  updateSession,
  evictIfNeeded,
  SESSION_TIMEOUT,
} from './session-manager.js';
import { validateExecuteParams } from './validator.js';
import { RUNTIME_NODE_MODULES, GLOBAL_NPM_MODULES } from './resolver.js';

const fastify = Fastify({ logger: true });

/**
 * Read installed packages from a node_modules directory
 */
function getInstalledPackages(nodeModulesPath) {
  if (!nodeModulesPath || !fs.existsSync(nodeModulesPath)) {
    return [];
  }

  const packages = [];
  try {
    const entries = fs.readdirSync(nodeModulesPath, { withFileTypes: true });
    for (const entry of entries) {
      if (!entry.isDirectory()) continue;

      // Handle scoped packages (@org/pkg)
      if (entry.name.startsWith('@')) {
        const scopePath = path.join(nodeModulesPath, entry.name);
        const scopedEntries = fs.readdirSync(scopePath, { withFileTypes: true });
        for (const scopedEntry of scopedEntries) {
          if (!scopedEntry.isDirectory()) continue;
          const pkgJsonPath = path.join(scopePath, scopedEntry.name, 'package.json');
          if (fs.existsSync(pkgJsonPath)) {
            try {
              const pkgJson = JSON.parse(fs.readFileSync(pkgJsonPath, 'utf-8'));
              packages.push({
                name: `${entry.name}/${scopedEntry.name}`,
                version: pkgJson.version || 'unknown',
              });
            } catch {
              // Skip if can't read package.json
            }
          }
        }
      } else {
        // Regular package
        const pkgJsonPath = path.join(nodeModulesPath, entry.name, 'package.json');
        if (fs.existsSync(pkgJsonPath)) {
          try {
            const pkgJson = JSON.parse(fs.readFileSync(pkgJsonPath, 'utf-8'));
            packages.push({
              name: entry.name,
              version: pkgJson.version || 'unknown',
            });
          } catch {
            // Skip if can't read package.json
          }
        }
      }
    }
  } catch {
    // Return empty array on error
  }

  return packages.sort((a, b) => a.name.localeCompare(b.name));
}

// Health check (lightweight)
fastify.get('/health', async () => ({
  status: 'ok',
  runtime: 'node',
  version: process.version,
}));

// Runtime info (detailed, includes packages)
fastify.get('/info', async () => {
  const runtimePackages = getInstalledPackages(RUNTIME_NODE_MODULES);
  const globalPackages = getInstalledPackages(GLOBAL_NPM_MODULES);

  return {
    status: 'ok',
    runtime: 'node',
    version: process.version,
    modulePaths: {
      runtime: RUNTIME_NODE_MODULES,
      globalNpm: GLOBAL_NPM_MODULES,
    },
    packages: {
      runtime: runtimePackages,
      global: globalPackages,
    },
    sessionConfig: {
      maxSessions: 20,
      defaultIdleTimeout: SESSION_TIMEOUT,
    },
  };
});

// List sessions
fastify.get('/sessions', async () => {
  const sessionList = listSessions();
  return { sessions: sessionList };
});

// Get session details
fastify.get('/sessions/:id', async (request, reply) => {
  const { id } = request.params;
  const session = sessions.get(id);

  if (!session) {
    reply.code(404);
    return { error: 'Session not found' };
  }

  return { session: session.getInfo() };
});

// Create session
fastify.post('/sessions', async (request) => {
  const { session_id, cwd, max_idle_time } = request.body || {};
  const id = session_id || randomUUID();

  if (sessions.has(id)) {
    return { session_id: id, created: false, message: 'Session already exists' };
  }

  // Evict oldest sessions if needed
  evictIfNeeded();

  // Convert max_idle_time from seconds to milliseconds if provided
  const maxIdleTimeMs = max_idle_time ? max_idle_time * 1000 : SESSION_TIMEOUT;

  const session = createSession(id, cwd || '/tmp', maxIdleTimeMs);
  sessions.set(id, session);

  return {
    session_id: id,
    created: true,
    session: session.getInfo(),
  };
});

// Update session
fastify.patch('/sessions/:id', async (request, reply) => {
  const { id } = request.params;
  const { max_idle_time, cwd } = request.body || {};

  const session = sessions.get(id);
  if (!session) {
    reply.code(404);
    return { error: 'Session not found' };
  }

  const updates = {};
  if (max_idle_time !== undefined) {
    // Convert from seconds to milliseconds
    updates.max_idle_time = max_idle_time * 1000;
  }
  if (cwd !== undefined) {
    updates.cwd = cwd;
  }

  if (Object.keys(updates).length > 0) {
    updateSession(id, updates);
  }

  return {
    updated: true,
    session: session.getInfo(),
  };
});

// Delete session
fastify.delete('/sessions/:id', async (request, reply) => {
  const { id } = request.params;

  const result = await closeSession(id);
  if (!result) {
    reply.code(404);
    return { error: 'Session not found' };
  }

  return { deleted: true };
});

// Execute code
fastify.post('/execute', async (request, reply) => {
  const { code, session_id, timeout = 30000, cwd } = request.body || {};

  if (!code) {
    reply.code(400);
    return { error: 'Code is required' };
  }

  const validationErrors = validateExecuteParams({ timeout, cwd, session_id });
  if (validationErrors.length > 0) {
    reply.code(400);
    return { error: 'Validation failed', details: validationErrors };
  }

  const sessionId = session_id || randomUUID();

  try {
    const result = await executeInSession(sessionId, code, timeout, cwd || '/tmp');

    return {
      session_id: sessionId,
      ...result,
    };
  } catch (err) {
    fastify.log.error(`Execution error: ${err.message}`);
    return {
      session_id: sessionId,
      success: false,
      stdout: '',
      stderr: err.message,
      result: '',
      error: {
        name: err.name || 'Error',
        message: err.message || String(err),
        stack: err.stack || '',
      },
    };
  }
});

// Start server
const PORT = process.env.NODEJS_REPL_PORT || 8092;

fastify.listen({ port: PORT, host: '127.0.0.1' }, (err, address) => {
  if (err) {
    fastify.log.error(err);
    process.exit(1);
  }
  fastify.log.info(`Node.js REPL Server listening on ${address}`);
});
