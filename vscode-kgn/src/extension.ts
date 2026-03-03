/**
 * KGN VS Code Extension — Entry Point
 *
 * Phase 11 Step 3: Language Client connected to kgn-lsp server.
 * Phase 11 Step 8: Graph Preview webview panel.
 * Phase 11 Step 9: Python interpreter resolution + graceful degradation.
 * Grammar registration is handled by package.json contributes.
 */
import * as vscode from "vscode";
import {
  LanguageClient,
  LanguageClientOptions,
  ServerOptions,
  TransportKind,
} from "vscode-languageclient/node";
import { showGraphPreview, updateGraphForActiveEditor } from "./preview";
import { initResolver, resolvePythonPath } from "./pythonResolver";

let outputChannel: vscode.OutputChannel | undefined;
let client: LanguageClient | undefined;
let statusItem: vscode.StatusBarItem | undefined;
/** Track whether the LSP-not-found warning was already shown this session. */
let lspWarningShown = false;

export function activate(context: vscode.ExtensionContext): void {
  outputChannel = vscode.window.createOutputChannel("KGN");
  outputChannel.appendLine("KGN extension activated");
  initResolver(outputChannel);

  // Register a status-bar item so the user knows the extension is loaded
  statusItem = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Right,
    100,
  );
  statusItem.text = "$(file-code) KGN";
  statusItem.tooltip = "KGN Knowledge-Graph Node support is active";
  statusItem.show();
  context.subscriptions.push(statusItem);

  // Start Language Client if enabled
  const config = vscode.workspace.getConfiguration("kgn");
  const lspEnabled = config.get<boolean>("lsp.enabled", true);

  if (lspEnabled) {
    startLanguageClient(context, config);
  } else {
    outputChannel.appendLine("LSP is disabled in settings.");
    setStatusTextMateOnly();
  }

  // Register Graph Preview command (Step 8)
  context.subscriptions.push(
    vscode.commands.registerCommand("kgn.showGraphPreview", () => {
      if (!client) {
        vscode.window.showWarningMessage("KGN LSP is not running.");
        return;
      }
      showGraphPreview(context, client);
    }),
  );

  // Auto-update graph preview when the active editor changes
  context.subscriptions.push(
    vscode.window.onDidChangeActiveTextEditor(() => {
      if (client) {
        updateGraphForActiveEditor(client);
      }
    }),
  );
}

/** Update status bar to indicate LSP is connected. */
function setStatusLspConnected(): void {
  if (statusItem) {
    statusItem.text = "$(check) KGN LSP";
    statusItem.tooltip = "KGN Language Server connected — full feature set active";
    statusItem.backgroundColor = undefined;
  }
}

/** Update status bar to indicate TextMate-only mode. */
function setStatusTextMateOnly(): void {
  if (statusItem) {
    statusItem.text = "$(warning) KGN (TextMate only)";
    statusItem.tooltip =
      "KGN Language Server not available — syntax highlighting and snippets only";
    statusItem.backgroundColor = new vscode.ThemeColor(
      "statusBarItem.warningBackground",
    );
  }
}

async function startLanguageClient(
  context: vscode.ExtensionContext,
  config: vscode.WorkspaceConfiguration,
): Promise<void> {
  // Resolve Python interpreter via the 4-stage resolver
  const pythonPath = await resolvePythonPath();

  if (!pythonPath) {
    outputChannel?.appendLine(
      "No Python interpreter with kgn[lsp] found. Running in TextMate-only mode.",
    );
    setStatusTextMateOnly();
    showLspNotFoundWarning();
    return;
  }

  const serverOptions: ServerOptions = {
    command: pythonPath,
    args: ["-c", "from kgn.lsp.server import server; server.start_io()"],
    transport: TransportKind.stdio,
  };

  const clientOptions: LanguageClientOptions = {
    documentSelector: [
      { scheme: "file", language: "kgn" },
      { scheme: "file", language: "kge" },
      { scheme: "untitled", language: "kgn" },
      { scheme: "untitled", language: "kge" },
    ],
    outputChannel: outputChannel!,
    traceOutputChannel: outputChannel!,
  };

  client = new LanguageClient(
    "kgn-lsp",
    "KGN Language Server",
    serverOptions,
    clientOptions,
  );

  // Register FileSystemWatchers for .kgn and .kge files
  const kgnWatcher = vscode.workspace.createFileSystemWatcher("**/*.kgn");
  const kgeWatcher = vscode.workspace.createFileSystemWatcher("**/*.kge");
  context.subscriptions.push(kgnWatcher);
  context.subscriptions.push(kgeWatcher);

  outputChannel?.appendLine(
    `Starting KGN LSP: ${pythonPath} -c "from kgn.lsp.server import server; server.start_io()"`,
  );

  try {
    await client.start();
    outputChannel?.appendLine("KGN LSP server started successfully.");
    setStatusLspConnected();
  } catch (err) {
    outputChannel?.appendLine(`Failed to start KGN LSP server: ${err}`);
    setStatusTextMateOnly();
    showLspNotFoundWarning();
    client = undefined;
  }

  context.subscriptions.push({
    dispose: () => {
      if (client) {
        client.stop();
      }
    },
  });
}

/**
 * Show a warning message (once per session) when the LSP is not available.
 */
function showLspNotFoundWarning(): void {
  if (lspWarningShown) {
    return;
  }
  lspWarningShown = true;

  vscode.window
    .showWarningMessage(
      "kgn LSP server not found. Install with: pip install kgn[lsp]",
      "Configure Path",
    )
    .then((selection) => {
      if (selection === "Configure Path") {
        vscode.commands.executeCommand(
          "workbench.action.openSettings",
          "kgn.pythonPath",
        );
      }
    });
}

export async function deactivate(): Promise<void> {
  if (client) {
    await client.stop();
    client = undefined;
  }
  outputChannel?.appendLine("KGN extension deactivated");
  outputChannel?.dispose();
  outputChannel = undefined;
}

