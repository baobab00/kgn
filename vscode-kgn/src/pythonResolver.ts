/**
 * Python Interpreter Resolver — 4-stage priority lookup.
 *
 * Phase 11 Step 9: Ensures the extension can locate a Python interpreter
 * that has `kgn[lsp]` installed, even across diverse environments
 * (venv, conda, global, pyenv).
 *
 * Resolution order:
 *   1. User setting  — `kgn.pythonPath` (workspace / user level)
 *   2. vscode-python — `ms-python.python` extension API
 *   3. PATH lookup   — `where kgn` / `which kgn` → derive Python
 *   4. System Python — `python3` / `python` with kgn importable
 *
 * If all stages fail, returns `null` → TextMate-only mode.
 */
import * as vscode from "vscode";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

/** Logging helper — writes to the shared output channel. */
let _log: vscode.OutputChannel | undefined;

/**
 * Initialise the resolver with an output channel for logging.
 */
export function initResolver(channel: vscode.OutputChannel): void {
  _log = channel;
}

function log(msg: string): void {
  _log?.appendLine(`[pythonResolver] ${msg}`);
}

// ── Stage helpers ────────────────────────────────────────────────────

/**
 * Verify that `pythonPath` can import `kgn.lsp.server`.
 * Returns `true` if the import succeeds, `false` otherwise.
 */
async function canImportKgn(pythonPath: string): Promise<boolean> {
  try {
    await execFileAsync(pythonPath, [
      "-c",
      "import kgn.lsp.server",
    ], { timeout: 10_000 });
    return true;
  } catch {
    return false;
  }
}

/**
 * Stage 1 — User setting `kgn.pythonPath`.
 */
async function fromUserSetting(): Promise<string | null> {
  const config = vscode.workspace.getConfiguration("kgn");
  const userPath = config.get<string>("pythonPath", "").trim();
  if (!userPath) {
    return null;
  }
  log(`Stage 1: user setting kgn.pythonPath = "${userPath}"`);
  if (await canImportKgn(userPath)) {
    log("Stage 1: ✓ kgn importable");
    return userPath;
  }
  log("Stage 1: ✗ kgn not importable at configured path");
  return null;
}

/**
 * Stage 2 — vscode-python extension API.
 *
 * Attempts to use the `ms-python.python` extension's
 * `environments.getActiveEnvironmentPath()` API to locate
 * the currently selected Python interpreter.
 */
async function fromVscodePython(): Promise<string | null> {
  const pyExt = vscode.extensions.getExtension("ms-python.python");
  if (!pyExt) {
    log("Stage 2: ms-python.python extension not installed");
    return null;
  }
  try {
    if (!pyExt.isActive) {
      await pyExt.activate();
    }
    const api = pyExt.exports;
    // The Python extension exposes environments.getActiveEnvironmentPath()
    const envPath = api?.environments?.getActiveEnvironmentPath?.();
    if (!envPath?.path) {
      log("Stage 2: no active environment path from vscode-python");
      return null;
    }
    const pythonPath = envPath.path;
    log(`Stage 2: vscode-python active env = "${pythonPath}"`);
    if (await canImportKgn(pythonPath)) {
      log("Stage 2: ✓ kgn importable");
      return pythonPath;
    }
    log("Stage 2: ✗ kgn not importable in vscode-python env");
  } catch (err) {
    log(`Stage 2: error querying vscode-python: ${err}`);
  }
  return null;
}

/**
 * Stage 3 — PATH lookup via `where` (Windows) or `which` (Unix).
 *
 * If `kgn` is on PATH, derive the Python that owns it by running
 * `kgn --version` with the parent's Python, or by inspecting the
 * shebang / wrapper script's directory.
 */
async function fromPathLookup(): Promise<string | null> {
  const isWindows = process.platform === "win32";
  const cmd = isWindows ? "where" : "which";
  try {
    const { stdout } = await execFileAsync(cmd, ["kgn"], { timeout: 5_000 });
    const kgnBin = stdout.trim().split(/\r?\n/)[0];
    if (!kgnBin) {
      return null;
    }
    log(`Stage 3: found kgn binary at "${kgnBin}"`);

    // Derive python from the same directory or parent
    // Typical layout: .venv/Scripts/kgn  → .venv/Scripts/python
    //                  .venv/bin/kgn     → .venv/bin/python
    const path = await import("node:path");
    const dir = path.dirname(kgnBin);
    const candidates = isWindows
      ? [path.join(dir, "python.exe"), path.join(dir, "python3.exe")]
      : [path.join(dir, "python3"), path.join(dir, "python")];

    for (const candidate of candidates) {
      if (await canImportKgn(candidate)) {
        log(`Stage 3: ✓ derived python = "${candidate}"`);
        return candidate;
      }
    }
    log("Stage 3: ✗ could not derive a working python from kgn binary");
  } catch {
    log("Stage 3: kgn not found on PATH");
  }
  return null;
}

/**
 * Stage 4 — System Python fallback.
 *
 * Tries `python3` then `python`, checking each can import kgn.
 */
async function fromSystemPython(): Promise<string | null> {
  const candidates = process.platform === "win32"
    ? ["python3", "python"]
    : ["python3", "python"];

  for (const cmd of candidates) {
    log(`Stage 4: trying "${cmd}"`);
    if (await canImportKgn(cmd)) {
      log(`Stage 4: ✓ kgn importable via "${cmd}"`);
      return cmd;
    }
  }
  log("Stage 4: ✗ no system python with kgn found");
  return null;
}

// ── Public API ───────────────────────────────────────────────────────

/**
 * Resolve the best available Python interpreter for the KGN LSP server.
 *
 * Walks through 4 stages in priority order.  Returns the path to
 * the Python executable, or `null` if none is found.
 */
export async function resolvePythonPath(): Promise<string | null> {
  log("Starting Python interpreter resolution...");

  // Stage 1: User setting
  const s1 = await fromUserSetting();
  if (s1) { return s1; }

  // Stage 2: vscode-python API
  const s2 = await fromVscodePython();
  if (s2) { return s2; }

  // Stage 3: PATH lookup
  const s3 = await fromPathLookup();
  if (s3) { return s3; }

  // Stage 4: System Python
  const s4 = await fromSystemPython();
  if (s4) { return s4; }

  log("All stages failed — no suitable Python interpreter found.");
  return null;
}
