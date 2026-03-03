/**
 * KGN Web — Node Detail Panel (R13: no build tools).
 *
 * Renders node metadata, Markdown body (via marked.js CDN),
 * and incoming/outgoing edge lists with click-to-navigate.
 */

/* global marked */

const DETAIL_API = '/api/v1';

// ── Detail panel controller ─────────────────────────────────────────

class DetailPanel {
    /**
     * @param {object} opts
     * @param {HTMLElement} opts.panel   - The aside#detail-panel element
     * @param {function}    opts.onNavigate - callback(nodeId) to navigate graph
     */
    constructor(opts) {
        this.panel = opts.panel;
        this.onNavigate = opts.onNavigate || (() => {});
        this._els = {
            title:    this.panel.querySelector('#detail-title'),
            meta:     this.panel.querySelector('#detail-meta'),
            body:     this.panel.querySelector('#detail-body'),
            edgeList: this.panel.querySelector('#detail-edges'),
        };
    }

    /** Show detail for a node (fetches full data + edges) */
    async show(nodeId) {
        this.panel.classList.add('open');
        this._setLoading(true);

        try {
            const [nodeRes, edgesRes] = await Promise.all([
                fetch(`${DETAIL_API}/nodes/${nodeId}`),
                fetch(`${DETAIL_API}/edges?node_id=${nodeId}`),
            ]);

            if (!nodeRes.ok) {
                this._setError('Node not found');
                return;
            }

            const node = await nodeRes.json();
            const edges = edgesRes.ok ? await edgesRes.json() : { incoming: [], outgoing: [], total: 0 };

            this._renderMeta(node);
            this._renderBody(node.body_md);
            this._renderEdges(edges);
        } catch (err) {
            console.error('Detail fetch error:', err);
            this._setError('Failed to load details');
        } finally {
            this._setLoading(false);
        }
    }

    /** Close the panel */
    close() {
        this.panel.classList.remove('open');
    }

    // ── Private rendering ───────────────────────────────────────────

    _renderMeta(node) {
        this._els.title.textContent = node.title;

        const typeColor = (window.TYPE_COLORS && window.TYPE_COLORS[node.type]) || '#888';
        this._els.meta.innerHTML = `
            <dt>Type</dt>
            <dd><span class="type-badge" style="background:${_escAttr(typeColor)}">${_esc(node.type)}</span></dd>
            <dt>Status</dt>
            <dd><span class="status-badge status-${_esc(node.status)}">${_esc(node.status)}</span></dd>
            <dt>ID</dt>
            <dd class="detail-id">${_esc(node.id)}</dd>
            <dt>Confidence</dt>
            <dd>${node.confidence != null ? node.confidence : '—'}</dd>
            <dt>Tags</dt>
            <dd>${(node.tags && node.tags.length) ? node.tags.map(t => `<span class="tag">${_esc(t)}</span>`).join(' ') : '—'}</dd>
            <dt>Agent</dt>
            <dd>${node.created_by ? _esc(node.created_by) : '—'}</dd>
            <dt>Created</dt>
            <dd>${node.created_at ? new Date(node.created_at).toLocaleString() : '—'}</dd>
            <dt>Updated</dt>
            <dd>${node.updated_at ? new Date(node.updated_at).toLocaleString() : '—'}</dd>
        `;
    }

    _renderBody(bodyMd) {
        const el = this._els.body;
        if (!bodyMd) {
            el.innerHTML = '<p class="empty">(no content)</p>';
            return;
        }

        // Use marked.js if available, otherwise show raw markdown
        if (typeof marked !== 'undefined' && marked.parse) {
            el.innerHTML = marked.parse(bodyMd);
        } else {
            el.textContent = bodyMd;
        }
    }

    _renderEdges(edgesData) {
        const el = this._els.edgeList;
        if (!el) return;

        const total = edgesData.total || 0;

        if (total === 0) {
            el.innerHTML = '<p class="empty">No edges</p>';
            return;
        }

        let html = `<h4>Edges (${total})</h4>`;

        if (edgesData.incoming.length > 0) {
            html += '<div class="edge-group"><h5>Incoming</h5><ul class="edge-list">';
            for (const e of edgesData.incoming) {
                html += this._edgeItem(e, 'incoming');
            }
            html += '</ul></div>';
        }

        if (edgesData.outgoing.length > 0) {
            html += '<div class="edge-group"><h5>Outgoing</h5><ul class="edge-list">';
            for (const e of edgesData.outgoing) {
                html += this._edgeItem(e, 'outgoing');
            }
            html += '</ul></div>';
        }

        el.innerHTML = html;

        // Attach click handlers
        el.querySelectorAll('[data-navigate-id]').forEach(link => {
            link.addEventListener('click', (evt) => {
                evt.preventDefault();
                const targetId = link.dataset.navigateId;
                this.onNavigate(targetId);
            });
        });
    }

    _edgeItem(edge, direction) {
        const arrow = direction === 'incoming' ? '←' : '→';
        const peerId = direction === 'incoming' ? edge.from_node_id : edge.to_node_id;
        const peerTitle = edge.peer_title || peerId.substring(0, 12) + '…';
        const note = edge.note ? ` <span class="edge-note">${_esc(edge.note)}</span>` : '';

        return `<li class="edge-item">
            <span class="edge-arrow">${arrow}</span>
            <span class="edge-type-tag edge-type-${_esc(edge.type)}">${_esc(edge.type)}</span>
            <a href="#" class="edge-peer" data-navigate-id="${_escAttr(peerId)}"
               title="${_escAttr(peerId)}">${_esc(peerTitle)}</a>${note}
        </li>`;
    }

    _setLoading(loading) {
        if (loading) {
            this._els.title.textContent = 'Loading…';
            this._els.meta.innerHTML = '';
            this._els.body.innerHTML = '';
            if (this._els.edgeList) this._els.edgeList.innerHTML = '';
        }
    }

    _setError(msg) {
        this._els.title.textContent = msg;
        this._els.meta.innerHTML = '';
        this._els.body.innerHTML = '';
        if (this._els.edgeList) this._els.edgeList.innerHTML = '';
    }
}

// ── Helpers ─────────────────────────────────────────────────────────

function _esc(s) {
    if (s == null) return '';
    const d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
}

function _escAttr(s) {
    if (s == null) return '';
    return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;')
                     .replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ── Export to global scope (R13: no build tools) ────────────────────

window.DetailPanel = DetailPanel;
