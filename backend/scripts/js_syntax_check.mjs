/**
 * Batch JS/JSX/TS syntax checker for generated files (Day 22).
 *
 * Reads {"files": {path: content}} from stdin, writes
 * {"results": {path: [{line, col, message}]}} to stdout. ONE process handles
 * the whole project: spawning node per file inside parallel coder workers is
 * the latency trap this batch interface exists to prevent.
 *
 * Why @babel/parser over acorn: plain acorn CANNOT parse JSX, so every
 * generated React component would report a syntax error and buy a paid
 * OpenRouter repair of an already-correct file. Babel handles JSX + TS with one
 * plugin list, installs as 4 pure-JS @babel packages (~5MB, no per-arch native
 * binary, unlike esbuild), and is the most tolerant of the options — and
 * tolerance is the cheap direction to be wrong in when false positives cost
 * money.
 *
 * Parse only, never execute: generated code is untrusted model output.
 */
import { parse } from "@babel/parser";

const TS_EXT = /\.tsx?$/i;
const JSX_CAPABLE = /\.(jsx|tsx|js|mjs)$/i;

function pluginsFor(path) {
  // JSX is enabled for .js too: generated React components routinely land in
  // .js files, and enabling it there costs nothing for plain JS.
  const plugins = [];
  if (JSX_CAPABLE.test(path)) plugins.push("jsx");
  if (TS_EXT.test(path)) plugins.push("typescript");
  return plugins;
}

function check(path, content) {
  if (!content || !content.trim()) {
    return [{ line: 0, col: 0, message: "file is empty" }];
  }
  try {
    parse(content, {
      sourceType: "module",
      sourceFilename: path,
      // errorRecovery off: we want a hard failure on genuinely broken syntax,
      // not a best-effort AST that would hide the defect we are looking for.
      errorRecovery: false,
      plugins: pluginsFor(path),
    });
    return [];
  } catch (err) {
    const loc = err.loc || {};
    return [{
      line: loc.line || 0,
      // Babel columns are 0-based; report 1-based to match ast/json/yaml.
      col: typeof loc.column === "number" ? loc.column + 1 : 0,
      // Babel appends "(line:col)" to the message; the caller renders location
      // itself, so strip it to avoid printing the position twice.
      message: String(err.message || "syntax error").replace(/\s*\(\d+:\d+\)\s*$/, ""),
    }];
  }
}

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return Buffer.concat(chunks).toString("utf8");
}

try {
  const payload = JSON.parse(await readStdin());
  const results = {};
  for (const [path, content] of Object.entries(payload.files || {})) {
    results[path] = check(path, content);
  }
  process.stdout.write(JSON.stringify({ results }));
} catch (err) {
  // Structural failure (bad stdin, missing parser). Exit non-zero so the Python
  // caller degrades LOUDLY rather than reading an empty result as "all clean".
  process.stderr.write(String(err && err.message ? err.message : err));
  process.exit(1);
}
