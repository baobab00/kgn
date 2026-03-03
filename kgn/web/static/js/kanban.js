/**
 * KGN Web — Kanban Board (R13: no build tools).
 *
 * Renders tasks grouped by state (READY / IN_PROGRESS / DONE / FAILED)
 * in a 4-column kanban board. IN_PROGRESS cards show lease countdown.
 */

const KANBAN_API = '/api/v1';

// ── State display configuration ─────────────────────────────────────

const KANBAN_STATES = ['READY', 'IN_PROGRESS', 'BLOCKED', 'DONE', 'FAILED'];

const STATE_CONFIG = {
    READY:       { label: 'Ready',       icon: '⏳', color: '#3b82f6' },
    IN_PROGRESS: { label: 'In Progress', icon: '🔄', color: '#f59e0b' },
    BLOCKED:     { label: 'Blocked',     icon: '🚫', color: '#6b7280' },
    DONE:        { label: 'Done',        icon: '✅', color: '#10b981' },
    FAILED:      { label: 'Failed',      icon: '❌', color: '#ef4444' },
};

// ── KanbanBoard class ───────────────────────────────────────────────

class KanbanBoard {
    /**
     * @param {string} containerId - DOM element ID for the kanban container
     * @param {object} opts
     * @param {function} opts.onCardClick - callback(task) when a card is clicked
     */
    constructor(containerId, opts) {
        this.container = document.getElementById(containerId);
        this.onCardClick = (opts && opts.onCardClick) || (() => {});
        this._countdownInterval = null;
    }

    /** Load & render tasks from the API */
    async load() {
        try {
            const res = await fetch(`${KANBAN_API}/tasks`);
            if (!res.ok) {
                this._showError('Failed to load tasks');
                return;
            }
            const data = await res.json();
            this._render(data.grouped, data.total);
            this._startCountdowns();
        } catch (err) {
            console.error('Kanban load error:', err);
            this._showError('Failed to load tasks');
        }
    }

    /** Refresh the board */
    async reload() {
        this._stopCountdowns();
        await this.load();
    }

    // ── Private rendering ───────────────────────────────────────────

    _render(grouped, total) {
        let html = '<div class="kanban-board">';

        for (const state of KANBAN_STATES) {
            const cfg = STATE_CONFIG[state];
            const tasks = grouped[state] || [];

            html += `<div class="kanban-column" data-state="${state}">
                <div class="kanban-column-header" style="border-top-color: ${cfg.color}">
                    <span class="kanban-state-icon">${cfg.icon}</span>
                    <span class="kanban-state-label">${cfg.label}</span>
                    <span class="kanban-count">${tasks.length}</span>
                </div>
                <div class="kanban-cards">`;

            for (const task of tasks) {
                html += this._renderCard(task, state);
            }

            if (tasks.length === 0) {
                html += '<div class="kanban-empty">No tasks</div>';
            }

            html += '</div></div>';
        }

        html += '</div>';
        this.container.innerHTML = html;

        // Attach card click handlers
        this.container.querySelectorAll('.kanban-card').forEach(card => {
            card.addEventListener('click', () => {
                const taskData = JSON.parse(card.dataset.task);
                this.onCardClick(taskData);
            });
        });
    }

    _renderCard(task, state) {
        const priorityClass = task.priority <= 3 ? 'priority-high' :
                              task.priority <= 7 ? 'priority-medium' : 'priority-low';

        let leaseHtml = '';
        if (state === 'IN_PROGRESS' && task.lease_expires_at) {
            const remaining = new Date(task.lease_expires_at) - Date.now();
            const leaseClass = remaining <= 300000 ? 'lease-warning' :
                               remaining <= 0 ? 'lease-expired' : '';
            leaseHtml = `<div class="card-lease ${leaseClass}" data-expires="${_escAttr(task.lease_expires_at)}">
                <span class="lease-label">Lease:</span>
                <span class="lease-time">${this._formatRemaining(remaining)}</span>
            </div>`;
        }

        let agentHtml = '';
        if (task.agent_key) {
            const roleClass = `role-${task.agent_role || 'worker'}`;
            agentHtml = `<div class="card-agent-info">
                <span class="agent-label">Agent:</span>
                <strong>${_esc(task.agent_key)}</strong>
                <span class="card-agent-role ${roleClass}">${_esc(task.agent_role || 'worker')}</span>
            </div>`;
        } else if (task.leased_by) {
            agentHtml = `<div class="card-agent"><span class="agent-label">Agent:</span> ${_esc(task.leased_by).substring(0, 12)}…</div>`;
        }

        const attemptsHtml = task.attempts > 0
            ? `<span class="card-attempts">${task.state === 'FAILED' ? '✗' : ''} attempts: ${task.attempts}/${task.max_attempts}</span>`
            : '';

        // Escape task data for data attribute
        const taskJson = _escAttr(JSON.stringify(task));

        return `<div class="kanban-card ${priorityClass}" data-task="${taskJson}" data-task-id="${_escAttr(task.id)}">
            <div class="card-title">${_esc(task.title)}</div>
            <div class="card-meta">
                <span class="card-priority">P${task.priority}</span>
                ${attemptsHtml}
            </div>
            ${leaseHtml}
            ${agentHtml}
            <div class="card-id">${task.task_node_id.substring(0, 12)}…</div>
        </div>`;
    }

    _formatRemaining(ms) {
        if (ms <= 0) return 'EXPIRED';
        const min = Math.floor(ms / 60000);
        if (min >= 60) {
            const h = Math.floor(min / 60);
            const m = min % 60;
            return `${h}h ${m}m`;
        }
        return `${min}m`;
    }

    // ── Lease countdown ─────────────────────────────────────────────

    _startCountdowns() {
        this._stopCountdowns();
        this._countdownInterval = setInterval(() => {
            this.container.querySelectorAll('.card-lease[data-expires]').forEach(el => {
                const expires = new Date(el.dataset.expires);
                const remaining = expires - Date.now();
                const timeEl = el.querySelector('.lease-time');
                if (timeEl) {
                    timeEl.textContent = this._formatRemaining(remaining);
                }

                el.classList.remove('lease-warning', 'lease-expired');
                if (remaining <= 0) {
                    el.classList.add('lease-expired');
                } else if (remaining <= 300000) { // 5 minutes
                    el.classList.add('lease-warning');
                }
            });
        }, 15000); // Update every 15 seconds
    }

    _stopCountdowns() {
        if (this._countdownInterval) {
            clearInterval(this._countdownInterval);
            this._countdownInterval = null;
        }
    }

    _showError(msg) {
        this.container.innerHTML = `<div class="kanban-error">${_esc(msg)}</div>`;
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

// ── Export (R13: no build tools) ────────────────────────────────────

window.KanbanBoard = KanbanBoard;
window.STATE_CONFIG = STATE_CONFIG;
