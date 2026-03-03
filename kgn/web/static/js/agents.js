/**
 * KGN Web — Agents View (R13: no build tools).
 *
 * Renders:
 *   - Agent card grid with role badges and task statistics
 *   - Activity timeline for selected agent
 *   - Agent performance bar chart (for Dashboard integration)
 *
 * API endpoints used:
 *   GET /api/v1/agents                      — agent list with stats
 *   GET /api/v1/agents/{id}/timeline        — activity timeline
 *   GET /api/v1/workflow/bottlenecks        — bottleneck tasks
 */

/* exported AgentsView */

const AGENTS_API = '/api/v1';

// ── Role colour mapping ─────────────────────────────────────────────

const ROLE_COLORS = {
    admin:    '#ef4444',
    worker:   '#3b82f6',
    reviewer: '#8b5cf6',
    indexer:  '#22c55e',
    genesis:  '#f59e0b',
};

// ── AgentsView class ────────────────────────────────────────────────

class AgentsView {
    /**
     * @param {string} containerId  DOM element id for the agents view
     */
    constructor(containerId) {
        this._container = document.getElementById(containerId);
        this._agents = [];
        this._selectedAgentId = null;
        this._pollTimer = null;
    }

    /** Load agents data and render the view. */
    async load() {
        try {
            const res = await fetch(`${AGENTS_API}/agents`);
            if (!res.ok) throw new Error(`Agents API error: ${res.status}`);
            const data = await res.json();
            this._agents = data.agents || [];
            this._render();
            this._startPolling();
        } catch (err) {
            this._container.innerHTML =
                `<div class="timeline-empty">Failed to load agents: ${_escAgent(err.message)}</div>`;
        }
    }

    /** Refresh data. */
    async reload() {
        try {
            const res = await fetch(`${AGENTS_API}/agents`);
            if (!res.ok) return;
            const data = await res.json();
            this._agents = data.agents || [];
            this._renderCards();
            // Re-select if agent was selected
            if (this._selectedAgentId) {
                this._loadTimeline(this._selectedAgentId);
            }
        } catch {
            // Silent fail on polling
        }
    }

    /** Stop auto-refresh. */
    destroy() {
        if (this._pollTimer) {
            clearInterval(this._pollTimer);
            this._pollTimer = null;
        }
    }

    // ── Private ─────────────────────────────────────────────────────

    _render() {
        this._container.innerHTML = `
            <div class="agents-layout">
                <div class="agents-list-section">
                    <h2 style="margin-top:0;font-size:1.1rem;">Agents
                        <span style="font-weight:400;font-size:0.85rem;color:#6b7280;">(${this._agents.length})</span>
                    </h2>
                    <div class="agents-grid" id="agents-grid"></div>
                </div>
                <div class="agents-timeline-section">
                    <div class="timeline-header" id="timeline-header">Select an agent to view timeline</div>
                    <div class="timeline-list" id="timeline-list">
                        <div class="timeline-empty">← Click an agent card</div>
                    </div>
                </div>
            </div>
        `;
        this._renderCards();
    }

    _renderCards() {
        const grid = document.getElementById('agents-grid');
        if (!grid) return;

        if (this._agents.length === 0) {
            grid.innerHTML = '<div class="timeline-empty">No agents found.</div>';
            return;
        }

        grid.innerHTML = this._agents.map(agent => {
            const roleClass = `role-${agent.role || 'worker'}`;
            const selected = agent.agent_id === this._selectedAgentId ? 'selected' : '';
            const avgTime = agent.avg_duration_sec > 0
                ? `${agent.avg_duration_sec.toFixed(1)}s avg`
                : 'no data';

            return `<div class="agent-card ${roleClass} ${selected}"
                         data-agent-id="${_escAttrAgent(agent.agent_id)}"
                         onclick="window._agentsView && window._agentsView._selectAgent('${_escAttrAgent(agent.agent_id)}')">
                <div class="agent-name">
                    ${_escAgent(agent.agent_key)}
                    <span class="agent-role-badge ${roleClass}">${_escAgent(agent.role || 'worker')}</span>
                </div>
                <div class="agent-stats-row">
                    <div class="agent-stat stat-done">
                        <span class="agent-stat-value">${agent.done_count}</span>
                        <span class="agent-stat-label">Done</span>
                    </div>
                    <div class="agent-stat stat-failed">
                        <span class="agent-stat-value">${agent.failed_count}</span>
                        <span class="agent-stat-label">Failed</span>
                    </div>
                    <div class="agent-stat stat-total">
                        <span class="agent-stat-value">${agent.total_tasks}</span>
                        <span class="agent-stat-label">Total</span>
                    </div>
                    <div class="agent-stat stat-rate">
                        <span class="agent-stat-value">${agent.success_rate.toFixed(0)}%</span>
                        <span class="agent-stat-label">Rate</span>
                    </div>
                </div>
                <div class="agent-avg-time">⏱ ${avgTime}</div>
            </div>`;
        }).join('');
    }

    _selectAgent(agentId) {
        this._selectedAgentId = agentId;

        // Update card selection styles
        document.querySelectorAll('.agent-card').forEach(card => {
            card.classList.toggle('selected', card.dataset.agentId === agentId);
        });

        this._loadTimeline(agentId);
    }

    async _loadTimeline(agentId) {
        const header = document.getElementById('timeline-header');
        const list = document.getElementById('timeline-list');
        if (!header || !list) return;

        const agent = this._agents.find(a => a.agent_id === agentId);
        const name = agent ? agent.agent_key : agentId.substring(0, 12);
        header.textContent = `Timeline — ${name}`;
        list.innerHTML = '<div class="timeline-empty">Loading…</div>';

        try {
            const res = await fetch(`${AGENTS_API}/agents/${agentId}/timeline?limit=20`);
            if (!res.ok) throw new Error(`Timeline API error: ${res.status}`);
            const data = await res.json();

            if (data.entries.length === 0) {
                list.innerHTML = '<div class="timeline-empty">No activities recorded.</div>';
                return;
            }

            list.innerHTML = data.entries.map(e => {
                const time = e.created_at ? e.created_at.substring(0, 19).replace('T', ' ') : '';
                const typeClass = e.activity_type || '';
                return `<div class="timeline-entry">
                    <div class="timeline-time">${_escAgent(time)}</div>
                    <div class="timeline-content">
                        <span class="timeline-type ${typeClass}">${_escAgent(e.activity_type)}</span>
                        <span class="timeline-message">${_escAgent(e.message || '')}</span>
                    </div>
                </div>`;
            }).join('');
        } catch (err) {
            list.innerHTML = `<div class="timeline-empty">Failed to load timeline: ${_escAgent(err.message)}</div>`;
        }
    }

    _startPolling() {
        if (this._pollTimer) clearInterval(this._pollTimer);
        this._pollTimer = setInterval(() => this.reload(), 30000);
    }
}

// ── Agent Performance Chart (Dashboard integration) ─────────────────

/**
 * Render agent performance bar chart into a given container.
 * Call this from DashboardView to add an agent chart.
 *
 * @param {string} canvasId  Canvas element ID for Chart.js
 * @param {Array}  agents    Array from GET /api/v1/agents
 * @returns {Chart|null}  Chart.js instance or null
 */
function renderAgentPerformanceChart(canvasId, agents) {
    if (typeof Chart === 'undefined' || !agents || agents.length === 0) return null;

    const ctx = document.getElementById(canvasId);
    if (!ctx) return null;

    const labels = agents.map(a => a.agent_key);
    const doneData = agents.map(a => a.done_count);
    const failedData = agents.map(a => a.failed_count);

    return new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [
                {
                    label: 'Done',
                    data: doneData,
                    backgroundColor: '#22c55e',
                    borderWidth: 0,
                },
                {
                    label: 'Failed',
                    data: failedData,
                    backgroundColor: '#ef4444',
                    borderWidth: 0,
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: 'top', labels: { boxWidth: 12, font: { size: 11 } } },
            },
            scales: {
                x: { stacked: false },
                y: { beginAtZero: true, ticks: { stepSize: 1 } },
            },
        },
    });
}

/**
 * Render task flow timeline chart.
 *
 * @param {string} canvasId  Canvas element ID
 * @param {Array}  tasks     Array from GET /api/v1/workflow/flow
 * @returns {Chart|null}
 */
function renderTaskFlowChart(canvasId, tasks) {
    if (typeof Chart === 'undefined' || !tasks || tasks.length === 0) return null;

    const ctx = document.getElementById(canvasId);
    if (!ctx) return null;

    // Sort by created_at and take top 20
    const sorted = tasks.slice().sort((a, b) =>
        (a.created_at || '').localeCompare(b.created_at || '')
    ).slice(-20);

    const labels = sorted.map(t => (t.task_title || '').substring(0, 15));
    const durations = sorted.map(t => t.duration_sec || 0);
    const colors = sorted.map(t =>
        t.state === 'DONE' ? '#22c55e' : t.state === 'FAILED' ? '#ef4444' : '#94a3b8'
    );

    return new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [{
                label: 'Duration (sec)',
                data: durations,
                backgroundColor: colors,
                borderWidth: 0,
            }],
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
            },
            scales: {
                x: { beginAtZero: true, title: { display: true, text: 'Seconds' } },
            },
        },
    });
}

// ── Helpers ─────────────────────────────────────────────────────────

function _escAgent(s) {
    if (s == null) return '';
    const d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
}

function _escAttrAgent(s) {
    if (s == null) return '';
    return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;')
                     .replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ── Export (R13: no build tools) ────────────────────────────────────

window.AgentsView = AgentsView;
window.renderAgentPerformanceChart = renderAgentPerformanceChart;
window.renderTaskFlowChart = renderTaskFlowChart;
window.ROLE_COLORS = ROLE_COLORS;
