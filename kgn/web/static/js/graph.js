/**
 * KGN Web — Cytoscape.js graph visualization (R13: no build tools).
 *
 * Fetches subgraph data from /api/v1/subgraph/{id} and renders
 * an interactive DAG with type-based colours, edge styles, and
 * k-hop viewport expansion.
 */

/* global cytoscape */

if (typeof window._KGN_API === 'undefined') window._KGN_API = '/api/v1';

// ── Node type → colour mapping ──────────────────────────────────────

const TYPE_COLORS = {
    GOAL:       '#FF6B6B',
    SPEC:       '#4ECDC4',
    ARCH:       '#45B7D1',
    LOGIC:      '#96CEB4',
    DECISION:   '#DAA520',
    ISSUE:      '#DDA0DD',
    TASK:       '#FF8C00',
    CONSTRAINT: '#708090',
    ASSUMPTION: '#D2691E',
    SUMMARY:    '#9370DB',
};

// ── Edge type → line style mapping ──────────────────────────────────

const EDGE_STYLES = {
    DEPENDS_ON:     { lineStyle: 'solid',  color: '#666',    arrow: 'triangle' },
    IMPLEMENTS:     { lineStyle: 'dashed', color: '#4ECDC4', arrow: 'triangle' },
    RESOLVES:       { lineStyle: 'solid',  color: '#96CEB4', arrow: 'diamond'  },
    SUPERSEDES:     { lineStyle: 'solid',  color: '#888',    arrow: 'tee'      },
    DERIVED_FROM:   { lineStyle: 'dashed', color: '#45B7D1', arrow: 'triangle' },
    CONTRADICTS:    { lineStyle: 'dotted', color: '#FF0000', arrow: 'triangle' },
    CONSTRAINED_BY: { lineStyle: 'dashed', color: '#708090', arrow: 'triangle' },
};

// ── Layout presets ──────────────────────────────────────────────────

const LAYOUTS = {
    dagre: {
        name: 'dagre',
        rankDir: 'TB',
        nodeSep: 60,
        rankSep: 80,
        animate: true,
        animationDuration: 300,
    },
    cola: {
        name: 'cola',
        animate: true,
        maxSimulationTime: 2000,
        nodeSpacing: 40,
    },
    concentric: {
        name: 'concentric',
        animate: true,
        animationDuration: 300,
        concentric: (node) => node.data('depth') != null ? (5 - node.data('depth')) : 1,
        levelWidth: () => 2,
    },
    circle: {
        name: 'circle',
        animate: true,
        animationDuration: 300,
    },
};

// ── Cytoscape stylesheet ────────────────────────────────────────────

function buildStylesheet() {
    const styles = [
        // Default node
        {
            selector: 'node',
            style: {
                'label': 'data(label)',
                'text-wrap': 'wrap',
                'text-max-width': '120px',
                'font-size': '11px',
                'text-valign': 'center',
                'text-halign': 'center',
                'width': 50,
                'height': 50,
                'shape': 'round-rectangle',
                'background-color': '#ccc',
                'border-width': 2,
                'border-color': '#999',
                'color': '#222',
            },
        },
        // Selected node
        {
            selector: 'node:selected',
            style: {
                'border-width': 4,
                'border-color': '#3b82f6',
                'background-opacity': 1,
            },
        },
        // Root node (depth=0)
        {
            selector: 'node[depth = 0]',
            style: {
                'width': 65,
                'height': 65,
                'font-weight': 'bold',
                'font-size': '12px',
                'border-width': 3,
            },
        },
        // Boundary nodes (highest depth) — slightly transparent
        {
            selector: '.boundary',
            style: {
                'opacity': 0.65,
                'border-style': 'dashed',
            },
        },
        // Default edge
        {
            selector: 'edge',
            style: {
                'width': 2,
                'line-color': '#999',
                'target-arrow-color': '#999',
                'target-arrow-shape': 'triangle',
                'curve-style': 'bezier',
                'label': 'data(label)',
                'font-size': '9px',
                'color': '#666',
                'text-rotation': 'autorotate',
                'text-margin-y': -10,
            },
        },
        // Selected edge
        {
            selector: 'edge:selected',
            style: {
                'width': 3,
                'line-color': '#3b82f6',
                'target-arrow-color': '#3b82f6',
            },
        },
        // Filtered-out nodes/edges (search filter)
        {
            selector: '.filtered-out',
            style: {
                'opacity': 0.2,
            },
        },
        // Conflict node (red border)
        {
            selector: '.conflict-node',
            style: {
                'border-width': 4,
                'border-color': '#ef4444',
            },
        },
        // Conflict edge (red dashed)
        {
            selector: 'edge[?conflict]',
            style: {
                'line-style': 'dashed',
                'line-color': '#ef4444',
                'target-arrow-color': '#ef4444',
                'target-arrow-shape': 'triangle',
                'width': 2.5,
                'font-size': '10px',
                'color': '#ef4444',
            },
        },
    ];

    // Type-specific node colours
    for (const [type, color] of Object.entries(TYPE_COLORS)) {
        styles.push({
            selector: `node[type = "${type}"]`,
            style: {
                'background-color': color,
                'border-color': _darken(color),
            },
        });
    }

    // Edge type styles
    for (const [type, cfg] of Object.entries(EDGE_STYLES)) {
        styles.push({
            selector: `edge[label = "${type}"]`,
            style: {
                'line-style': cfg.lineStyle,
                'line-color': cfg.color,
                'target-arrow-color': cfg.color,
                'target-arrow-shape': cfg.arrow,
            },
        });
    }

    return styles;
}

/** Darken a hex colour by ~25% for borders */
function _darken(hex) {
    const r = Math.max(0, parseInt(hex.slice(1, 3), 16) - 40);
    const g = Math.max(0, parseInt(hex.slice(3, 5), 16) - 40);
    const b = Math.max(0, parseInt(hex.slice(5, 7), 16) - 40);
    return `#${r.toString(16).padStart(2,'0')}${g.toString(16).padStart(2,'0')}${b.toString(16).padStart(2,'0')}`;
}

// ── KgnGraph class ──────────────────────────────────────────────────

class KgnGraph {
    constructor(containerId) {
        this.containerId = containerId;
        this.cy = null;
        this.currentRootId = null;
        this.currentDepth = 2;
        this.loadedNodeIds = new Set();
        this._onNodeSelect = null; // callback(nodeData)
    }

    /** Initialise Cytoscape in the container */
    init() {
        this.cy = cytoscape({
            container: document.getElementById(this.containerId),
            style: buildStylesheet(),
            layout: { name: 'grid' }, // temporary; real layout applied on data load
            minZoom: 0.2,
            maxZoom: 4,
            wheelSensitivity: 0.3,
        });

        // Resize Cytoscape when its container changes size
        const container = document.getElementById(this.containerId);
        if (typeof ResizeObserver !== 'undefined' && container) {
            this._resizeObserver = new ResizeObserver(() => {
                if (this.cy) this.cy.resize();
            });
            this._resizeObserver.observe(container);
        }

        // Single-click → select node → show detail
        this.cy.on('tap', 'node', (evt) => {
            const data = evt.target.data();
            if (this._onNodeSelect) this._onNodeSelect(data);
        });

        // Double-click boundary node → expand subgraph
        this.cy.on('dbltap', 'node.boundary', (evt) => {
            const nodeId = evt.target.data('id');
            this.expand(nodeId);
        });

        // Click background → deselect
        this.cy.on('tap', (evt) => {
            if (evt.target === this.cy) {
                this.cy.elements().unselect();
            }
        });
    }

    /** Set callback for node selection */
    onNodeSelect(fn) {
        this._onNodeSelect = fn;
    }

    /** Load a subgraph centred on rootId */
    async load(rootId, depth) {
        depth = depth || this.currentDepth;
        this.currentRootId = rootId;
        this.currentDepth = depth;
        this.loadedNodeIds.clear();

        const data = await this._fetch(rootId, depth);
        if (!data) return;

        this.cy.elements().remove();
        this.cy.add(data.elements.nodes);
        this.cy.add(data.elements.edges);

        // Track loaded IDs
        data.elements.nodes.forEach(n => this.loadedNodeIds.add(n.data.id));

        // Mark boundary nodes
        this._markBoundary(depth);

        // Apply layout (use currently selected, default to dagre)
        const layoutSel = document.getElementById('graph-layout');
        const layoutName = (layoutSel && layoutSel.value) || 'dagre';
        try {
            this.applyLayout(layoutName);
        } catch (err) {
            console.warn('Layout failed, falling back to grid:', err);
            this.cy.layout({ name: 'grid' }).run();
        }

        // Show truncation warning
        this._showTruncation(data.truncated, data.total_nodes, data.rendered_nodes);

        // Select root
        const rootEl = this.cy.getElementById(rootId);
        if (rootEl.length) {
            rootEl.select();
            if (this._onNodeSelect) this._onNodeSelect(rootEl.data());
        }
    }

    /** Expand from a boundary node (merge into existing graph) */
    async expand(nodeId) {
        const data = await this._fetch(nodeId, 1);
        if (!data) return;

        // Add only new nodes
        const newNodes = data.elements.nodes.filter(
            n => !this.loadedNodeIds.has(n.data.id)
        );
        const allIds = new Set(this.loadedNodeIds);
        newNodes.forEach(n => allIds.add(n.data.id));

        // Add new edges if both endpoints exist
        const newEdges = data.elements.edges.filter(
            e => allIds.has(e.data.source) && allIds.has(e.data.target)
                && this.cy.getElementById(e.data.id).length === 0
        );

        if (newNodes.length === 0 && newEdges.length === 0) return;

        this.cy.add(newNodes);
        this.cy.add(newEdges);
        newNodes.forEach(n => this.loadedNodeIds.add(n.data.id));

        // Remove boundary class from expanded node
        this.cy.getElementById(nodeId).removeClass('boundary');

        // Mark new boundaries
        this._markNewBoundary(newNodes);

        // Re-layout (use currently selected)
        const layoutSel = document.getElementById('graph-layout');
        const layoutName = (layoutSel && layoutSel.value) || 'dagre';
        try {
            this.applyLayout(layoutName);
        } catch (err) {
            console.warn('Layout failed on expand, falling back to grid:', err);
            this.cy.layout({ name: 'grid' }).run();
        }

        // Truncation check
        this._showTruncation(
            this.loadedNodeIds.size > 200,
            this.loadedNodeIds.size,
            this.loadedNodeIds.size
        );
    }

    /** Apply a named layout */
    applyLayout(name) {
        const preset = LAYOUTS[name];
        if (!preset) return;
        this.cy.layout(preset).run();
    }

    /** Centre the view on a node */
    focusNode(nodeId) {
        const el = this.cy.getElementById(nodeId);
        if (el.length) {
            this.cy.animate({
                center: { eles: el },
                zoom: 1.5,
                duration: 300,
            });
            el.select();
        }
    }

    // ── Private helpers ─────────────────────────────────────────────

    async _fetch(nodeId, depth) {
        try {
            const res = await fetch(`${window._KGN_API}/subgraph/${nodeId}?depth=${depth}`);
            if (!res.ok) {
                console.error('Subgraph fetch failed:', res.status);
                return null;
            }
            return await res.json();
        } catch (err) {
            console.error('Subgraph fetch error:', err);
            return null;
        }
    }

    _markBoundary(depth) {
        this.cy.nodes().forEach(n => {
            if (n.data('depth') === depth) {
                n.addClass('boundary');
            }
        });
    }

    _markNewBoundary(newNodes) {
        // New leaf nodes (no outgoing edges to other new nodes) are boundaries
        const newIds = new Set(newNodes.map(n => n.data.id));
        newNodes.forEach(n => {
            const el = this.cy.getElementById(n.data.id);
            const neighbors = el.neighborhood('node');
            const hasNewNeighbor = neighbors.some(nb => newIds.has(nb.data('id')) && nb.data('id') !== n.data.id);
            if (!hasNewNeighbor) {
                el.addClass('boundary');
            }
        });
    }

    _showTruncation(truncated, total, rendered) {
        const el = document.getElementById('truncation-warning');
        if (!el) return;
        if (truncated) {
            el.textContent = `Graph truncated: showing ${rendered} of ${total} nodes (max 200). Double-click boundary nodes to expand.`;
            el.classList.add('visible');
        } else {
            el.classList.remove('visible');
        }
    }
}

// ── Export to global scope (no build tools — R13) ───────────────────

window.KgnGraph = KgnGraph;
window.TYPE_COLORS = TYPE_COLORS;
