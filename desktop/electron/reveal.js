'use strict';

// Validates file-reveal requests from the renderer before Electron shows
// the path in the OS file manager. Pure logic (path + fs injected) so it
// stays unit-testable without Electron on every platform.

function resolveRevealTarget(input, {path, fs, platform} = {}) {
  const tools = path || require('path');
  const files = fs || require('fs');
  const current = platform || process.platform;
  if (typeof input !== 'string') return {ok: false, reason: 'type'};
  const target = input.trim();
  if (!target || target.length > 4096 || target.includes('\0')) return {ok: false, reason: 'shape'};
  const absolute = current === 'win32' ? tools.win32.isAbsolute(target) : tools.posix.isAbsolute(target);
  if (!absolute) return {ok: false, reason: 'relative'};
  let stats = null;
  try {
    stats = files.statSync(target);
  } catch {
    return {ok: false, reason: 'missing'};
  }
  if (!stats.isFile()) return {ok: false, reason: 'not_file'};
  return {ok: true, path: target};
}

module.exports = {resolveRevealTarget};
