/**
 * KGN Web — Search, Filter & Similar Node Visualization (R13: no build tools).
 *
 * Provides:
 *  - SearchFilterBar: type/status/tags multi-select + text search
 *  - Graph filter overlay: fade non-matching nodes to 20% opacity
 *  - Similar node highlight: size + opacity + gold border by similarity
 *  - Conflict display: red dashed edges + red-bordered node pairs
 */

/* global API */

// ── SearchFilterBar ─────────────────────────────────────────────────

class SearchFilterBar {
    /**
     * @param {string} containerId - DOM element ID	for the filter bar
     * @param {object} opts
     * @param {function} opts.onFilter  - callback(filters) when filters change
     * @param {function} opts.onSimilar - callback(nodeId) for similar search
     */
    constructor(containerId, opts = {}) {
        this.container = document.getElementById(containerId);
        this.onFilter = opts.onFilter || (() => {});
        this.onSimilar = opts.onSimilar || (() => {});
        this._selectedTypes = new Set();
        this._selectedStatuses = new Set();
        this._selectedTags = new Set();
        this._searchText = '';
        this._allTags = [];
        this._visible = false;
    }

    /** Render the filter bar UI */
    render() {
        this.container.innerHTML = `
            <div class="search-bar">
                <div class="search-row">
                    <div class="search-input-wrap">
                        <input type="search" id="sf-text" placeholder="Search nodes by title…"
                               aria-label="Search nodes" autocomplete="off">
                    </div>
                    <button id="sf-toggle-filters" class="sf-btn" title="Toggle filters">
                        &#x25BC; Filters
                    </button>
                    <button id="sf-clear" class="sf-btn sf-btn-secondary" title="Clear all filters">
                        Clear
                    </button>
                </div>
                <div id="sf-filters" class="sf-filters hidden">
                    <div class="sf-filter-group">
                        <label>Type</label>
                        <div id="sf-types" class="sf-chips"></div>
                    </div>
                    <div class="sf-filter-group">
                        <label>Status</label>
                        <div id="sf-statuses" class="sf-chips"></div>
                    </div>
                    <div class="sf-filter-group">
                        <label>Tags</label>
                        <div id="sf-tags" class="sf-chips"></div>
                    </div>
                </div>
                <div id="sf-active-filters" class="sf-active-filters"></div>
            </div>
        `;

        this._renderChips();
        this._bindEvents();
    }

    /** Update available tags from node data */
    setTags(tags) {
        this._allTags = [...new Set(tags)].sort();
        this._renderTagChips();
    }

    /** Get current filter state */
    getFilters() {
        return {
            types: [...this._selectedTypes],
            statuses: [...this._selectedStatuses],
            tags: [...this._selectedTags],
            text: this._searchText,
        };
    }

    /** Check if any filter is active */
    hasActiveFilters() {
        return this._selectedTypes.size > 0
            || this._selectedStatuses.size > 0
            || this._selectedTags.size > 0
            || this._searchText.length > 0;
    }

    /** Clear all filters */
    clear() {
        this._selectedTypes.clear();
        this._selectedStatuses.clear();
        this._selectedTags.clear();
        this._searchText = '';
        const input = document.getElementById('sf-text');
        if (input) input.value = '';
        this._updateChipStates();
        this._renderActiveFilters();
        this.onFilter(this.getFilters());
    }

    // ── Private ─────────────────────────────────────────────────────

    _renderChips() {
        const TYPES = ['GOAL','SPEC','ARCH','LOGIC','DECISION','ISSUE','TASK','CONSTRAINT','ASSUMPTION','SUMMARY'];
        const STATUSES = ['ACTIVE','DEPRECATED','SUPERSEDED','ARCHIVED'];
        const TYPE_COLORS = window.TYPE_CHART_COLORS || window.TYPE_COLORS || {};

        const typesEl = document.getElementById('sf-types');
        if (typesEl) {
            typesEl.innerHTML = TYPES.map(t => {
                const color = TYPE_COLORS[t] || '#888';
                return `<button class="sf-chip" data-filter="type" data-value="${t}"
                         style="--chip-color:${color}">${t}</button>`;
            }).join('');
        }

        const statusEl = document.getElementById('sf-statuses');
        if (statusEl) {
            statusEl.innerHTML = STATUSES.map(s =>
                `<button class="sf-chip" data-filter="status" data-value="${s}">${s}</button>`
            ).join('');
        }

        this._renderTagChips();
    }

    _renderTagChips() {
        const tagsEl = document.getElementById('sf-tags');
        if (!tagsEl) return;
        if (this._allTags.length === 0) {
            tagsEl.innerHTML = '<span class="sf-no-tags">No tags found</span>';
            return;
        }
        tagsEl.innerHTML = this._allTags.map(t =>
            `<button class="sf-chip sf-chip-tag" data-filter="tag" data-value="${t}">${t}</button>`
        ).join('');
        // Restore selected state
        this._updateChipStates();
    }

    _bindEvents() {
        // Text search with debounce
        const input = document.getElementById('sf-text');
        let timer = null;
        if (input) {
            input.addEventListener('input', () => {
                clearTimeout(timer);
                timer = setTimeout(() => {
                    this._searchText = input.value.trim();
                    this._renderActiveFilters();
                    this.onFilter(this.getFilters());
                }, 300);
            });
        }

        // Toggle filters panel
        const toggleBtn = document.getElementById('sf-toggle-filters');
        if (toggleBtn) {
            toggleBtn.addEventListener('click', () => {
                const panel = document.getElementById('sf-filters');
                if (panel) {
                    this._visible = !this._visible;
                    panel.classList.toggle('hidden', !this._visible);
                    toggleBtn.innerHTML = this._visible ? '&#x25B2; Filters' : '&#x25BC; Filters';
                }
            });
        }

        // Clear button
        const clearBtn = document.getElementById('sf-clear');
        if (clearBtn) {
            clearBtn.addEventListener('click', () => this.clear());
        }

        // Chip clicks (event delegation)
        this.container.addEventListener('click', (e) => {
            const chip = e.target.closest('.sf-chip');
            if (!chip) return;
            const filter = chip.dataset.filter;
            const value = chip.dataset.value;
            if (!filter || !value) return;

            if (filter === 'type') {
                this._toggle(this._selectedTypes, value);
            } else if (filter === 'status') {
                this._toggle(this._selectedStatuses, value);
            } else if (filter === 'tag') {
                this._toggle(this._selectedTags, value);
            }

            this._updateChipStates();
            this._renderActiveFilters();
            this.onFilter(this.getFilters());
        });
    }

    _toggle(set, value) {
        if (set.has(value)) set.delete(value);
        else set.add(value);
    }

    _updateChipStates() {
        this.container.querySelectorAll('.sf-chip').forEach(chip => {
            const filter = chip.dataset.filter;
            const value = chip.dataset.value;
            let active = false;
            if (filter === 'type') active = this._selectedTypes.has(value);
            else if (filter === 'status') active = this._selectedStatuses.has(value);
            else if (filter === 'tag') active = this._selectedTags.has(value);
            chip.classList.toggle('active', active);
        });
    }

    _renderActiveFilters() {
        const el = document.getElementById('sf-active-filters');
        if (!el) return;
        const parts = [];
        if (this._selectedTypes.size > 0) {
            parts.push(`Type: ${[...this._selectedTypes].join(', ')}`);
        }
        if (this._selectedStatuses.size > 0) {
            parts.push(`Status: ${[...this._selectedStatuses].join(', ')}`);
        }
        if (this._selectedTags.size > 0) {
            parts.push(`Tags: ${[...this._selectedTags].join(', ')}`);
        }
        if (this._searchText) {
            parts.push(`Search: "${this._searchText}"`);
        }
        el.textContent = parts.length > 0 ? parts.join(' | ') : '';
    }
}


// ── Graph filter overlay ────────────────────────────────────────────

/**
 * Apply filter overlay to a Cytoscape graph instance.
 * Matching nodes stay normal; non-matching nodes fade to 20% opacity.
 *
 * @param {object} cy - Cytoscape instance
 * @param {object} filters - { types, statuses, tags, text }
 */
function applyGraphFilter(cy, filters) {
    if (!cy) return;

    const hasFilter = filters.types.length > 0
        || filters.statuses.length > 0
        || filters.tags.length > 0
        || filters.text.length > 0;

    if (!hasFilter) {
        // Reset all nodes to normal
        cy.nodes().removeClass('filtered-out');
        cy.edges().removeClass('filtered-out');
        return;
    }

    cy.nodes().forEach(node => {
        const data = node.data();
        let match = true;

        if (filters.types.length > 0 && !filters.types.includes(data.type)) {
            match = false;
        }
        if (match && filters.statuses.length > 0 && !filters.statuses.includes(data.status)) {
            match = false;
        }
        if (match && filters.tags.length > 0) {
            const nodeTags = data.tags || [];
            if (!filters.tags.some(t => nodeTags.includes(t))) {
                match = false;
            }
        }
        if (match && filters.text.length > 0) {
            const label = (data.label || data.title || '').toLowerCase();
            if (!label.includes(filters.text.toLowerCase())) {
                match = false;
            }
        }

        if (match) {
            node.removeClass('filtered-out');
        } else {
            node.addClass('filtered-out');
        }
    });

    // Edges: keep visible if at least one endpoint matches
    cy.edges().forEach(edge => {
        const src = edge.source();
        const tgt = edge.target();
        if (src.hasClass('filtered-out') && tgt.hasClass('filtered-out')) {
            edge.addClass('filtered-out');
        } else {
            edge.removeClass('filtered-out');
        }
    });
}


// ── Similar node highlight ──────────────────────────────────────────

/**
 * Highlight similar nodes on the graph with size/opacity/gold border.
 *
 * @param {object} cy - Cytoscape instance
 * @param {Array} results - [{ id, similarity, ... }]
 */
function highlightSimilar(cy, results) {
    if (!cy) return;

    // Clear previous highlights
    clearSimilarHighlight(cy);

    results.forEach(({ id, similarity }) => {
        const node = cy.getElementById(id);
        if (node && node.length) {
            node.addClass('similar-highlight');
            const size = 20 + similarity * 40;   // 20–60px
            const opacity = 0.3 + similarity * 0.7; // 0.3–1.0
            node.style({
                'width': size,
                'height': size,
                'opacity': opacity,
                'border-width': 3,
                'border-color': '#FFD700',
            });
        }
    });
}

/**
 * Clear similar node highlights.
 * @param {object} cy - Cytoscape instance
 */
function clearSimilarHighlight(cy) {
    if (!cy) return;
    cy.nodes('.similar-highlight').forEach(node => {
        node.removeClass('similar-highlight');
        node.removeStyle('width height opacity border-width border-color');
    });
}


// ── Conflict display ────────────────────────────────────────────────

/**
 * Display conflict pairs on the graph.
 *
 * @param {object} cy - Cytoscape instance
 * @param {Array} conflicts - [{ node_a_id, node_b_id, similarity, status }]
 */
function showConflicts(cy, conflicts) {
    if (!cy) return;

    // Clear previous conflict markers
    clearConflicts(cy);

    conflicts.forEach(({ node_a_id, node_b_id, similarity, status }) => {
        const nodeA = cy.getElementById(node_a_id);
        const nodeB = cy.getElementById(node_b_id);

        // Mark nodes with red border
        if (nodeA && nodeA.length) nodeA.addClass('conflict-node');
        if (nodeB && nodeB.length) nodeB.addClass('conflict-node');

        // Add conflict edge if both nodes present and no edge exists
        if (nodeA && nodeA.length && nodeB && nodeB.length) {
            const edgeId = `conflict-${node_a_id}-${node_b_id}`;
            if (cy.getElementById(edgeId).length === 0) {
                cy.add({
                    group: 'edges',
                    data: {
                        id: edgeId,
                        source: node_a_id,
                        target: node_b_id,
                        label: `⚠ ${(similarity * 100).toFixed(0)}%`,
                        conflict: true,
                        status: status,
                    },
                });
            }
        }
    });
}

/**
 * Clear conflict markers from the graph.
 * @param {object} cy - Cytoscape instance
 */
function clearConflicts(cy) {
    if (!cy) return;
    cy.nodes('.conflict-node').removeClass('conflict-node');
    cy.edges('[?conflict]').remove();
}


// ── Export to global scope (no build tools — R13) ───────────────────

window.SearchFilterBar = SearchFilterBar;
window.applyGraphFilter = applyGraphFilter;
window.highlightSimilar = highlightSimilar;
window.clearSimilarHighlight = clearSimilarHighlight;
window.showConflicts = showConflicts;
window.clearConflicts = clearConflicts;
