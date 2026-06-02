/**
 * Request Validator
 */

import * as path from 'node:path';

export const MIN_TIMEOUT = 100;
export const MAX_TIMEOUT = 300000;

/**
 * Validate execute request parameters
 */
export function validateExecuteParams(params) {
  const errors = [];

  if (params.timeout !== undefined) {
    if (typeof params.timeout !== 'number' || !Number.isFinite(params.timeout)) {
      errors.push('timeout must be a finite number');
    } else if (params.timeout < MIN_TIMEOUT) {
      errors.push(`timeout must be at least ${MIN_TIMEOUT}ms`);
    } else if (params.timeout > MAX_TIMEOUT) {
      errors.push(`timeout must not exceed ${MAX_TIMEOUT}ms`);
    }
  }

  if (params.cwd !== undefined && params.cwd !== null) {
    if (typeof params.cwd !== 'string') {
      errors.push('cwd must be a string');
    } else if (!path.isAbsolute(params.cwd)) {
      errors.push('cwd must be an absolute path');
    } else if (params.cwd.includes('..')) {
      errors.push('cwd must not contain path traversal (..)');
    }
  }

  if (params.session_id !== undefined && params.session_id !== null) {
    if (typeof params.session_id !== 'string') {
      errors.push('session_id must be a string');
    } else if (params.session_id.length === 0) {
      errors.push('session_id must not be empty');
    } else if (params.session_id.length > 128) {
      errors.push('session_id must not exceed 128 characters');
    }
  }

  return errors;
}
