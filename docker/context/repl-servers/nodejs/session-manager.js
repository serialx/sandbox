/**
 * Session Manager
 *
 * Manages Node.js REPL sessions using Worker Threads.
 * Each session runs in its own Worker Thread for true isolation and timeout handling.
 *
 * Key Features:
 * - True timeout termination: Worker.terminate() kills both sync and async code
 * - State persistence: Variables persist within a session until timeout/deletion
 * - Note: On timeout, session state is lost and a fresh context is created
 *
 * Session States:
 * - IDLE: Ready to execute code
 * - EXECUTING: Currently running code
 */

import { Worker } from 'node:worker_threads';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

// Get directory of this file for worker path resolution
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Worker script path
const WORKER_PATH = path.join(__dirname, 'worker.js');

// Session configuration
export const SESSION_TIMEOUT = 24 * 60 * 60 * 1000; // Default 24 hours idle timeout
export const MAX_SESSIONS = 20;

// Session states
export const SessionState = {
  IDLE: 'IDLE',
  EXECUTING: 'EXECUTING',
};

// Sessions map
export const sessions = new Map();

/**
 * Session class representing a single REPL session
 */
class Session {
  constructor(id, cwd = '/tmp', maxIdleTime = SESSION_TIMEOUT) {
    this.id = id;
    this.cwd = cwd;
    this.createdAt = Date.now();
    this.lastUsed = Date.now();
    this.maxIdleTime = maxIdleTime;
    this.state = SessionState.IDLE;
    this.worker = null;
    this.workerReady = false;
    this.pendingCallbacks = new Map();
    this.callbackId = 0;
  }

  /**
   * Spawn a new worker thread
   */
  async _spawnWorker() {
    // Terminate existing worker if any
    if (this.worker) {
      try {
        await this.worker.terminate();
      } catch {
        // Ignore termination errors
      }
    }

    this.workerReady = false;

    return new Promise((resolve, reject) => {
      // Create worker with session data
      this.worker = new Worker(WORKER_PATH, {
        workerData: {
          sessionId: this.id,
          cwd: this.cwd,
        },
      });

      // Handle worker ready
      const onReady = (message) => {
        if (message.type === 'ready') {
          this.workerReady = true;
          resolve();
        }
      };

      // Handle worker messages
      this.worker.on('message', (message) => {
        // Check if this is the ready message
        if (message.type === 'ready') {
          onReady(message);
          return;
        }

        // Route response to pending callback
        if (this.pendingCallbacks.size > 0) {
          // Get the oldest pending callback (FIFO)
          const firstKey = this.pendingCallbacks.keys().next().value;
          const callback = this.pendingCallbacks.get(firstKey);
          if (callback) {
            this.pendingCallbacks.delete(firstKey);
            callback(message);
          }
        }
      });

      // Handle worker error
      this.worker.on('error', (err) => {
        console.error(`Worker error for session ${this.id}:`, err);
        // Reject if still waiting for ready
        if (!this.workerReady) {
          reject(err);
        }
      });

      // Handle worker exit
      this.worker.on('exit', (code) => {
        if (code !== 0) {
          console.error(`Worker for session ${this.id} exited with code ${code}`);
        }
        this.workerReady = false;
      });
    });
  }

  /**
   * Ensure worker is ready
   */
  async ensureWorker() {
    if (!this.worker || !this.workerReady) {
      await this._spawnWorker();
    }
  }

  /**
   * Execute code with timeout handling
   *
   * @param {string} code - Code to execute
   * @param {number} timeout - Timeout in milliseconds
   * @returns {Promise<object>} - Execution result
   */
  async execute(code, timeout = 30000) {
    // Ensure worker is ready
    await this.ensureWorker();

    this.lastUsed = Date.now();
    this.state = SessionState.EXECUTING;

    return new Promise((resolve) => {
      let resolved = false;
      let timeoutTimer = null;

      // Timeout handler - terminates worker
      const handleTimeout = async () => {
        if (resolved) return;
        resolved = true;

        // Remove callback from pending map to prevent message routing issues
        this.pendingCallbacks.delete(callbackId);

        // Terminate the worker
        try {
          await this.worker.terminate();
        } catch {
          // Ignore termination errors
        }
        this.worker = null;
        this.workerReady = false;

        // Spawn new worker with fresh context
        try {
          await this._spawnWorker();
        } catch (err) {
          console.error(`Failed to respawn worker for session ${this.id}:`, err);
        }

        this.state = SessionState.IDLE;

        resolve({
          success: false,
          stdout: '',
          stderr: `TimeoutError: Execution timed out after ${timeout}ms. Session state has been reset.`,
          result: '',
          error: {
            name: 'TimeoutError',
            message: `Execution timed out after ${timeout}ms. Session state has been reset.`,
            stack: '',
          },
        });
      };

      // Set up timeout timer
      timeoutTimer = setTimeout(handleTimeout, timeout);

      // Set up response handler
      const callbackId = ++this.callbackId;
      this.pendingCallbacks.set(callbackId, (response) => {
        if (resolved) return;
        resolved = true;
        clearTimeout(timeoutTimer);

        this.state = SessionState.IDLE;

        // Normal response
        resolve({
          success: response.success,
          stdout: response.stdout || '',
          stderr: response.stderr || '',
          result: response.result || '',
          error: response.error || null,
        });
      });

      // Send execute message
      this.worker.postMessage({
        type: 'execute',
        code,
        timeout,
      });
    });
  }

  /**
   * Close the session and terminate worker
   */
  async close() {
    if (this.worker) {
      try {
        await this.worker.terminate();
      } catch {
        // Ignore termination errors
      }
      this.worker = null;
      this.workerReady = false;
    }
    this.pendingCallbacks.clear();
  }

  /**
   * Get session info
   */
  getInfo() {
    return {
      session_id: this.id,
      cwd: this.cwd,
      created_at: this.createdAt,
      last_used: this.lastUsed,
      max_idle_time: this.maxIdleTime,
      age_seconds: Math.floor((Date.now() - this.lastUsed) / 1000),
      state: this.state,
    };
  }

  /**
   * Update session configuration
   */
  update(config) {
    if (config.max_idle_time !== undefined) {
      this.maxIdleTime = config.max_idle_time;
    }
    if (config.cwd !== undefined) {
      this.cwd = config.cwd;
      // Need to re-initialize worker with new cwd
      if (this.worker && this.workerReady) {
        this.worker.postMessage({
          type: 'init',
          sessionId: this.id,
          cwd: this.cwd,
        });
      }
    }
  }
}

/**
 * Create a new session
 *
 * @param {string} id - Session ID
 * @param {string} cwd - Working directory
 * @param {number} maxIdleTime - Maximum idle time in milliseconds
 * @returns {Session}
 */
export function createSession(id, cwd = '/tmp', maxIdleTime = SESSION_TIMEOUT) {
  const session = new Session(id, cwd, maxIdleTime);
  return session;
}

/**
 * Get session by ID
 *
 * @param {string} id - Session ID
 * @returns {Session|undefined}
 */
export function getSession(id) {
  return sessions.get(id);
}

/**
 * Delete a session
 *
 * @param {string} id - Session ID
 * @returns {boolean}
 */
export async function closeSession(id) {
  const session = sessions.get(id);
  if (session) {
    await session.close();
    sessions.delete(id);
    return true;
  }
  return false;
}

/**
 * Execute code in a session
 *
 * @param {string} sessionId - Session ID
 * @param {string} code - Code to execute
 * @param {number} timeout - Timeout in milliseconds
 * @param {string} cwd - Working directory (for new sessions)
 * @returns {Promise<object>}
 */
export async function executeInSession(sessionId, code, timeout = 30000, cwd = '/tmp') {
  let session = sessions.get(sessionId);

  // Create session if it doesn't exist
  if (!session) {
    // Evict if needed
    evictIfNeeded();
    session = createSession(sessionId, cwd);
    sessions.set(sessionId, session);
  }

  return session.execute(code, timeout);
}

/**
 * List all sessions
 *
 * @returns {object}
 */
export function listSessions() {
  const result = {};
  for (const [id, session] of sessions) {
    result[id] = session.getInfo();
  }
  return result;
}

/**
 * Update session configuration
 *
 * @param {string} id - Session ID
 * @param {object} config - Configuration to update
 * @returns {boolean}
 */
export function updateSession(id, config) {
  const session = sessions.get(id);
  if (session) {
    session.update(config);
    return true;
  }
  return false;
}

/**
 * Evict oldest sessions if over limit (LRU strategy)
 */
export function evictIfNeeded() {
  if (sessions.size < MAX_SESSIONS) {
    return 0;
  }

  const sorted = Array.from(sessions.entries()).sort(
    (a, b) => a[1].lastUsed - b[1].lastUsed
  );

  const toRemove = sessions.size - MAX_SESSIONS + 1;
  let removed = 0;

  for (let i = 0; i < toRemove; i++) {
    const [id, session] = sorted[i];
    session.close();
    sessions.delete(id);
    console.log(`Evicted oldest session ${id} to make room`);
    removed++;
  }

  return removed;
}

/**
 * Cleanup expired sessions
 */
export async function cleanupExpiredSessions() {
  const now = Date.now();
  let expiredCount = 0;

  for (const [id, session] of sessions) {
    if (now - session.lastUsed > session.maxIdleTime) {
      await session.close();
      sessions.delete(id);
      expiredCount++;
    }
  }

  if (expiredCount > 0) {
    console.log(`Cleaned up ${expiredCount} expired sessions`);
  }
}

// Cleanup interval
setInterval(cleanupExpiredSessions, 60 * 1000);
