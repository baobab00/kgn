/**
 * KGN Web — Dashboard (Chart.js stats visualization).
 *
 * Renders:
 *   - Summary stat cards (Nodes, Edges, Active, Tasks, Health Index)
 *   - Node Types doughnut chart
 *   - Task Pipeline horizontal bar chart
 *   - Health Metrics detail list
 *   - 30-second auto-refresh polling
 */

/* exported DashboardView */

class DashboardView {
  /**
   * @param {string} containerId  DOM element id to render into
   */
  constructor(containerId) {
    this._container = document.getElementById(containerId);
    this._typesChart = null;
    this._pipelineChart = null;
    this._agentChart = null;
    this._flowChart = null;
    this._pollTimer = null;
    this._data = null;
  }

  /** Fetch stats and render full dashboard. */
  async load() {
    try {
      const [statsRes, agentsRes, flowRes] = await Promise.all([
        fetch('/api/v1/stats'),
        fetch('/api/v1/agents').catch(() => null),
        fetch('/api/v1/workflow/flow').catch(() => null),
      ]);
      if (!statsRes.ok) throw new Error(`Stats API error: ${statsRes.status}`);
      this._data = await statsRes.json();
      this._data._agents = agentsRes && agentsRes.ok ? (await agentsRes.json()).agents : [];
      this._data._flow = flowRes && flowRes.ok ? (await flowRes.json()).tasks : [];
      this._render(this._data);
      this._startPolling();
    } catch (err) {
      this._container.innerHTML =
        `<div class="dash-error">Failed to load dashboard: ${err.message}</div>`;
    }
  }

  /** Refresh data without full DOM rebuild. */
  async reload() {
    try {
      const res = await fetch('/api/v1/stats');
      if (!res.ok) return;
      this._data = await res.json();
      this._updateCards(this._data);
      this._updateCharts(this._data);
      this._updateMetrics(this._data);
    } catch {
      // Silent fail on polling — will retry next interval
    }
  }

  /** Stop auto-refresh polling. */
  destroy() {
    if (this._pollTimer) {
      clearInterval(this._pollTimer);
      this._pollTimer = null;
    }
    if (this._typesChart) { this._typesChart.destroy(); this._typesChart = null; }
    if (this._pipelineChart) { this._pipelineChart.destroy(); this._pipelineChart = null; }
    if (this._agentChart) { this._agentChart.destroy(); this._agentChart = null; }
    if (this._flowChart) { this._flowChart.destroy(); this._flowChart = null; }
  }

  // ── Private ───────────────────────────────────────────────────────

  _render(data) {
    const totalTasks = Object.values(data.task_pipeline || {}).reduce((a, b) => a + b, 0);

    this._container.innerHTML = `
      <div class="dash-cards" id="dash-cards">
        ${this._cardHTML('Nodes', data.total_nodes, 'dash-card-nodes')}
        ${this._cardHTML('Edges', data.total_edges, 'dash-card-edges')}
        ${this._cardHTML('Active', data.active_nodes, 'dash-card-active')}
        ${this._cardHTML('Tasks', totalTasks, 'dash-card-tasks')}
        ${this._healthCardHTML(data.health_index)}
      </div>

      <div class="dash-charts">
        <div class="dash-chart-box">
          <h3>Node Types</h3>
          <canvas id="dash-types-chart"></canvas>
        </div>
        <div class="dash-chart-box">
          <h3>Task Pipeline</h3>
          <canvas id="dash-pipeline-chart"></canvas>
        </div>
        <div class="dash-chart-box">
          <h3>Agent Performance</h3>
          <canvas id="dash-agent-chart"></canvas>
        </div>
        <div class="dash-chart-box">
          <h3>Task Flow (Duration)</h3>
          <canvas id="dash-flow-chart"></canvas>
        </div>
      </div>

      <div class="dash-metrics" id="dash-metrics">
        <h3>Health Metrics</h3>
        ${this._metricsHTML(data)}
      </div>

      <div class="dash-refresh-info">
        Auto-refresh: 30s
        <span id="dash-last-update">${new Date().toLocaleTimeString('en-US')}</span>
      </div>
    `;

    this._initCharts(data);

    // Agent charts (from agents.js)
    if (window.renderAgentPerformanceChart && data._agents && data._agents.length > 0) {
      this._agentChart = window.renderAgentPerformanceChart('dash-agent-chart', data._agents);
    }
    if (window.renderTaskFlowChart && data._flow && data._flow.length > 0) {
      this._flowChart = window.renderTaskFlowChart('dash-flow-chart', data._flow);
    }
  }

  _cardHTML(label, value, cls) {
    return `<div class="dash-card ${cls || ''}">
      <div class="dash-card-value">${value}</div>
      <div class="dash-card-label">${label}</div>
    </div>`;
  }

  _healthCardHTML(index) {
    const pct = Math.round(index * 100);
    const color = pct >= 80 ? '#22c55e' : pct >= 50 ? '#eab308' : '#ef4444';
    return `<div class="dash-card dash-card-health">
      <div class="dash-card-value" style="color:${color}">${pct}%</div>
      <div class="dash-health-bar">
        <div class="dash-health-fill" style="width:${pct}%;background:${color}"></div>
      </div>
      <div class="dash-card-label">Health Index</div>
    </div>`;
  }

  _metricsHTML(data) {
    const m = data.health_metrics || {};
    const items = [
      { label: 'Orphan Rate', value: `${(m.orphan_rate * 100).toFixed(1)}%`, ok: m.orphan_rate < 0.2 },
      { label: 'Orphan Count', value: m.orphan_count, ok: m.orphan_count === 0 },
      { label: 'Conflicts', value: m.conflict_count, ok: m.conflict_count === 0 },
      { label: 'WIP Tasks', value: m.wip_tasks, ok: true },
      { label: 'Stale Superseded', value: m.superseded_stale, ok: m.superseded_stale === 0 },
      { label: 'Dup Spec Rate', value: `${(m.dup_spec_rate * 100).toFixed(1)}%`, ok: m.dup_spec_rate < 0.1 },
      { label: 'Open Assumptions', value: m.open_assumptions, ok: true },
    ];
    return `<dl class="dash-metric-list">
      ${items.map(i =>
        `<div class="dash-metric-item ${i.ok ? '' : 'dash-metric-warn'}">
          <dt>${i.label}</dt><dd>${i.value}</dd>
        </div>`
      ).join('')}
    </dl>`;
  }

  _updateCards(data) {
    const cards = document.getElementById('dash-cards');
    if (!cards) return;
    const totalTasks = Object.values(data.task_pipeline || {}).reduce((a, b) => a + b, 0);
    cards.innerHTML = `
      ${this._cardHTML('Nodes', data.total_nodes, 'dash-card-nodes')}
      ${this._cardHTML('Edges', data.total_edges, 'dash-card-edges')}
      ${this._cardHTML('Active', data.active_nodes, 'dash-card-active')}
      ${this._cardHTML('Tasks', totalTasks, 'dash-card-tasks')}
      ${this._healthCardHTML(data.health_index)}
    `;
  }

  _updateMetrics(data) {
    const el = document.getElementById('dash-metrics');
    if (!el) return;
    el.innerHTML = `<h3>Health Metrics</h3>${this._metricsHTML(data)}`;
    const ts = document.getElementById('dash-last-update');
    if (ts) ts.textContent = new Date().toLocaleTimeString('en-US');
  }

  _initCharts(data) {
    if (typeof Chart === 'undefined') return;

    // Node Types doughnut
    const typeLabels = Object.keys(data.node_types || {});
    const typeValues = Object.values(data.node_types || {});
    const typeColors = typeLabels.map(t => TYPE_CHART_COLORS[t] || '#94a3b8');

    const typesCtx = document.getElementById('dash-types-chart');
    if (typesCtx) {
      this._typesChart = new Chart(typesCtx, {
        type: 'doughnut',
        data: {
          labels: typeLabels,
          datasets: [{
            data: typeValues,
            backgroundColor: typeColors,
            borderWidth: 1,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { position: 'right', labels: { boxWidth: 12, font: { size: 11 } } },
          },
        },
      });
    }

    // Task Pipeline horizontal bar
    const PIPELINE_STATES = ['READY', 'IN_PROGRESS', 'BLOCKED', 'DONE', 'FAILED'];
    const PIPELINE_COLORS = {
      READY: '#3b82f6', IN_PROGRESS: '#eab308', BLOCKED: '#6b7280',
      DONE: '#22c55e', FAILED: '#ef4444',
    };
    const pipeLabels = PIPELINE_STATES;
    const pipeValues = PIPELINE_STATES.map(s => (data.task_pipeline || {})[s] || 0);
    const pipeColors = PIPELINE_STATES.map(s => PIPELINE_COLORS[s]);

    const pipeCtx = document.getElementById('dash-pipeline-chart');
    if (pipeCtx) {
      this._pipelineChart = new Chart(pipeCtx, {
        type: 'bar',
        data: {
          labels: pipeLabels,
          datasets: [{
            data: pipeValues,
            backgroundColor: pipeColors,
            borderWidth: 0,
          }],
        },
        options: {
          indexAxis: 'y',
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { beginAtZero: true, ticks: { stepSize: 1 } },
          },
        },
      });
    }
  }

  _updateCharts(data) {
    // Update doughnut
    if (this._typesChart) {
      const typeLabels = Object.keys(data.node_types || {});
      const typeValues = Object.values(data.node_types || {});
      this._typesChart.data.labels = typeLabels;
      this._typesChart.data.datasets[0].data = typeValues;
      this._typesChart.data.datasets[0].backgroundColor =
        typeLabels.map(t => TYPE_CHART_COLORS[t] || '#94a3b8');
      this._typesChart.update('none');
    }

    // Update pipeline bar
    if (this._pipelineChart) {
      const PIPELINE_STATES = ['READY', 'IN_PROGRESS', 'BLOCKED', 'DONE', 'FAILED'];
      this._pipelineChart.data.datasets[0].data =
        PIPELINE_STATES.map(s => (data.task_pipeline || {})[s] || 0);
      this._pipelineChart.update('none');
    }
  }

  _startPolling() {
    if (this._pollTimer) clearInterval(this._pollTimer);
    this._pollTimer = setInterval(() => this.reload(), 30000);
  }
}

// ── Chart color mapping per node type ─────────────────────────────────
const TYPE_CHART_COLORS = {
  GOAL: '#ef4444',
  SPEC: '#3b82f6',
  ARCH: '#8b5cf6',
  LOGIC: '#06b6d4',
  DECISION: '#f59e0b',
  ISSUE: '#ec4899',
  TASK: '#22c55e',
  CONSTRAINT: '#64748b',
  ASSUMPTION: '#f97316',
  SUMMARY: '#14b8a6',
};

window.DashboardView = DashboardView;
window.TYPE_CHART_COLORS = TYPE_CHART_COLORS;
