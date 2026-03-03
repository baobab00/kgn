/**
 * KGN Graph Preview — Webview Panel
 *
 * Phase 11 Step 8: Cytoscape.js-based subgraph visualisation.
 * Sends `kgn/subgraph` custom requests to the language server and
 * renders the result in a VS Code WebviewPanel.
 */
import * as vscode from "vscode";
import { LanguageClient } from "vscode-languageclient/node";

/** Colour map for node types (mirrors subgraph_handler.NODE_TYPE_COLOURS). */
const NODE_TYPE_COLOURS: Record<string, string> = {
  GOAL: "#4A90D9",
  SPEC: "#27AE60",
  ARCH: "#8E44AD",
  LOGIC: "#E67E22",
  DECISION: "#E74C3C",
  ISSUE: "#F39C12",
  TASK: "#3498DB",
  CONSTRAINT: "#95A5A6",
  ASSUMPTION: "#1ABC9C",
  SUMMARY: "#34495E",
};

interface SubgraphNode {
  id: string;
  type: string;
  title: string;
  status: string;
  slug: string;
  colour: string;
  path: string;
}

interface SubgraphEdge {
  from: string;
  to: string;
  type: string;
}

interface SubgraphResponse {
  centre: string;
  nodes: SubgraphNode[];
  edges: SubgraphEdge[];
  truncated: boolean;
}

let currentPanel: vscode.WebviewPanel | undefined;

/**
 * Show (or re-use) the graph preview panel.
 */
export function showGraphPreview(
  context: vscode.ExtensionContext,
  client: LanguageClient | undefined,
): void {
  if (currentPanel) {
    currentPanel.reveal(vscode.ViewColumn.Beside);
  } else {
    currentPanel = vscode.window.createWebviewPanel(
      "kgnGraphPreview",
      "KGN Graph Preview",
      vscode.ViewColumn.Beside,
      {
        enableScripts: true,
        retainContextWhenHidden: true,
      },
    );

    currentPanel.onDidDispose(() => {
      currentPanel = undefined;
    });

    // Handle messages from the webview (node click → open file)
    currentPanel.webview.onDidReceiveMessage(
      async (msg: { command: string; path?: string }) => {
        if (msg.command === "openFile" && msg.path) {
          const uri = vscode.Uri.file(msg.path);
          const doc = await vscode.workspace.openTextDocument(uri);
          await vscode.window.showTextDocument(doc, vscode.ViewColumn.One);
        }
      },
    );
  }

  currentPanel.webview.html = getWebviewContent();

  // Send initial graph for the active editor
  updateGraphForActiveEditor(client);
}

/**
 * Update the graph when the active editor changes.
 */
export function updateGraphForActiveEditor(
  client: LanguageClient | undefined,
): void {
  if (!currentPanel || !client) {
    return;
  }

  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    return;
  }

  const uri = editor.document.uri;
  if (!uri.fsPath.endsWith(".kgn")) {
    return; // Keep last graph for non-.kgn files
  }

  // Extract node ID from the document text
  const text = editor.document.getText();
  const nodeId = extractNodeId(text);
  if (!nodeId) {
    return;
  }

  // Send kgn/subgraph request
  client
    .sendRequest("kgn/subgraph", { nodeId, depth: 2, maxNodes: 50 })
    .then((response: unknown) => {
      const data = response as SubgraphResponse;
      if (currentPanel) {
        currentPanel.webview.postMessage({
          command: "updateGraph",
          data,
        });
      }
    })
    .catch((err: Error) => {
      console.error("kgn/subgraph request failed:", err);
    });
}

/**
 * Extract the node ID from a .kgn document's front matter.
 */
function extractNodeId(text: string): string | undefined {
  const lines = text.split("\n");
  let inFrontMatter = false;

  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed === "---") {
      if (inFrontMatter) break;
      inFrontMatter = true;
      continue;
    }
    if (!inFrontMatter) continue;

    if (trimmed.startsWith("id:")) {
      const value = trimmed.slice(3).trim().replace(/^["']|["']$/g, "");
      return value || undefined;
    }
  }
  return undefined;
}

/**
 * Generate the Webview HTML with embedded Cytoscape.js.
 */
function getWebviewContent(): string {
  // Build node type legend entries
  const legendItems = Object.entries(NODE_TYPE_COLOURS)
    .map(
      ([type, colour]) =>
        `<span class="legend-item"><span class="legend-dot" style="background:${colour}"></span>${type}</span>`,
    )
    .join("\n        ");

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>KGN Graph Preview</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      background: var(--vscode-editor-background, #1e1e1e);
      color: var(--vscode-editor-foreground, #d4d4d4);
      font-family: var(--vscode-font-family, 'Segoe UI', sans-serif);
      height: 100vh;
      display: flex;
      flex-direction: column;
    }
    #toolbar {
      padding: 8px 12px;
      display: flex;
      gap: 8px;
      align-items: center;
      border-bottom: 1px solid var(--vscode-panel-border, #333);
      font-size: 12px;
      flex-wrap: wrap;
    }
    #toolbar label { color: var(--vscode-descriptionForeground, #888); }
    #toolbar select, #toolbar input {
      background: var(--vscode-input-background, #3c3c3c);
      color: var(--vscode-input-foreground, #ccc);
      border: 1px solid var(--vscode-input-border, #555);
      padding: 2px 6px;
      border-radius: 3px;
      font-size: 12px;
    }
    #cy { flex: 1; }
    #legend {
      padding: 4px 12px;
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      font-size: 11px;
      border-top: 1px solid var(--vscode-panel-border, #333);
    }
    .legend-item { display: flex; align-items: center; gap: 4px; }
    .legend-dot {
      width: 10px; height: 10px;
      border-radius: 50%;
      display: inline-block;
    }
    #placeholder {
      display: flex;
      align-items: center;
      justify-content: center;
      flex: 1;
      color: var(--vscode-descriptionForeground, #888);
      font-size: 14px;
    }
    .truncated-badge {
      background: var(--vscode-editorWarning-foreground, #e6a700);
      color: #000;
      padding: 2px 6px;
      border-radius: 3px;
      font-size: 11px;
    }
  </style>
</head>
<body>
  <div id="toolbar">
    <label>Layout:</label>
    <select id="layout-select">
      <option value="dagre" selected>Dagre (hierarchy)</option>
      <option value="cose">Cose (force)</option>
      <option value="circle">Circle</option>
    </select>
    <span id="truncated-msg" style="display:none" class="truncated-badge">
      ⚠ Graph truncated to max nodes
    </span>
  </div>
  <div id="cy"></div>
  <div id="placeholder" style="display:none">
    Open a .kgn file to see its subgraph
  </div>
  <div id="legend">
    ${legendItems}
  </div>

  <script src="https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.30.4/cytoscape.min.js"></script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/dagre/0.8.5/dagre.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/cytoscape-dagre@2.5.0/cytoscape-dagre.min.js"></script>
  <script>
    const vscode = acquireVsCodeApi();

    // Register dagre layout
    if (typeof cytoscapeDagre === 'function') {
      cytoscapeDagre(cytoscape);
    }

    let cy = null;

    function initCytoscape(elements) {
      if (cy) cy.destroy();

      cy = cytoscape({
        container: document.getElementById('cy'),
        elements: elements,
        style: [
          {
            selector: 'node',
            style: {
              'label': 'data(label)',
              'text-wrap': 'wrap',
              'text-max-width': '120px',
              'text-valign': 'center',
              'text-halign': 'center',
              'background-color': 'data(colour)',
              'color': '#fff',
              'font-size': '10px',
              'width': 50,
              'height': 50,
              'border-width': 2,
              'border-color': '#555',
              'shape': 'roundrectangle',
              'padding': '6px',
            }
          },
          {
            selector: 'node.centre',
            style: {
              'border-width': 4,
              'border-color': '#FFD700',
              'width': 60,
              'height': 60,
            }
          },
          {
            selector: 'edge',
            style: {
              'width': 2,
              'line-color': '#666',
              'target-arrow-color': '#666',
              'target-arrow-shape': 'triangle',
              'curve-style': 'bezier',
              'label': 'data(label)',
              'font-size': '8px',
              'color': '#888',
              'text-rotation': 'autorotate',
            }
          }
        ],
        layout: { name: 'dagre', rankDir: 'TB', spacingFactor: 1.3 },
        wheelSensitivity: 0.3,
      });

      // Node click → open file
      cy.on('tap', 'node', function(evt) {
        const path = evt.target.data('path');
        if (path) {
          vscode.postMessage({ command: 'openFile', path: path });
        }
      });
    }

    function updateGraph(data) {
      const cyEl = document.getElementById('cy');
      const placeholderEl = document.getElementById('placeholder');
      const truncEl = document.getElementById('truncated-msg');

      if (!data || !data.nodes || data.nodes.length === 0) {
        cyEl.style.display = 'none';
        placeholderEl.style.display = 'flex';
        truncEl.style.display = 'none';
        return;
      }

      cyEl.style.display = 'block';
      placeholderEl.style.display = 'none';
      truncEl.style.display = data.truncated ? 'inline' : 'none';

      const elements = [];
      for (const n of data.nodes) {
        const el = {
          group: 'nodes',
          data: {
            id: n.id,
            label: n.title.length > 25 ? n.title.slice(0, 22) + '...' : n.title,
            colour: n.colour,
            path: n.path,
            type: n.type,
          },
          classes: n.id === data.centre ? 'centre' : '',
        };
        elements.push(el);
      }
      for (const e of data.edges) {
        elements.push({
          group: 'edges',
          data: {
            source: e.from,
            target: e.to,
            label: e.type,
          },
        });
      }

      initCytoscape(elements);
      applyLayout();
    }

    function applyLayout() {
      if (!cy) return;
      const name = document.getElementById('layout-select').value;
      const opts = { name };
      if (name === 'dagre') {
        opts.rankDir = 'TB';
        opts.spacingFactor = 1.3;
      }
      cy.layout(opts).run();
    }

    document.getElementById('layout-select').addEventListener('change', applyLayout);

    // Listen for messages from the extension
    window.addEventListener('message', function(event) {
      const msg = event.data;
      if (msg.command === 'updateGraph') {
        updateGraph(msg.data);
      }
    });
  </script>
</body>
</html>`;
}
