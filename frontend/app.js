'use strict';

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------
const API = '/api/v1';

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let currentUser = null;
let currentProject = null;
let projects = [];
let selectedWebhookId = null;
let currentPage = 1;
let destinations = [];
let ws = null;
let wsReconnectTimer = null;
let searchTimer = null;

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', async () => {
  await boot();
});

async function boot() {
  try {
    const me = await apiFetch('/auth/me');
    currentUser = me;
    renderUserMenu();
    await loadProjects();
    connectWS();
    navTo('overview');
  } catch {
    // Not logged in → redirect
    window.location.href = '/login.html';
  }
}

// ---------------------------------------------------------------------------
// API fetch helper (uses httpOnly cookie automatically)
// ---------------------------------------------------------------------------
async function apiFetch(path, opts = {}) {
  const res = await fetch(API + path, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  });
  if (res.status === 401) { window.location.href = '/login.html'; throw new Error('401'); }
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try { const j = await res.json(); detail = j.detail || detail; } catch {}
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------
function renderUserMenu() {
  if (!currentUser) return;
  const email = currentUser.email || '';
  document.getElementById('user-initial').textContent = email[0]?.toUpperCase() || '?';
  document.getElementById('user-email-short').textContent = email.split('@')[0];
  document.getElementById('user-email-full').textContent = email;
  // Toggle dropdowns
  document.getElementById('user-menu-trigger').addEventListener('click', (e) => {
    e.stopPropagation();
    document.getElementById('user-dropdown').classList.toggle('open');
    document.getElementById('ps-dropdown').classList.remove('open');
  });
}

async function logout() {
  try { await apiFetch('/auth/logout', { method: 'POST' }); } catch {}
  window.location.href = '/login.html';
}

// ---------------------------------------------------------------------------
// Projects
// ---------------------------------------------------------------------------
async function loadProjects() {
  try {
    projects = await apiFetch('/projects');
    if (projects.length === 0) {
      // Auto-create first project
      const p = await apiFetch('/projects', { method: 'POST', body: JSON.stringify({ name: 'My Project' }) });
      projects = [p];
    }
    currentProject = projects[0];
    renderProjectSwitcher();
    await afterProjectSwitch();
  } catch (e) {
    toast('Failed to load projects: ' + e.message, 'error');
  }
}

function renderProjectSwitcher() {
  document.getElementById('ps-name').textContent = currentProject?.name || '…';
  const list = document.getElementById('ps-list');
  list.innerHTML = projects.map(p => `
    <div class="ps-item ${p.id === currentProject?.id ? 'active' : ''}" onclick="switchProject('${p.id}')">
      <span>📁</span> ${esc(p.name)}
    </div>
  `).join('');
  document.getElementById('ps-trigger').addEventListener('click', (e) => {
    e.stopPropagation();
    document.getElementById('ps-dropdown').classList.toggle('open');
    document.getElementById('user-dropdown').classList.remove('open');
  });
}

async function switchProject(id) {
  currentProject = projects.find(p => p.id === id) || currentProject;
  renderProjectSwitcher();
  document.getElementById('ps-dropdown').classList.remove('open');
  await afterProjectSwitch();
  reconnectWS();
}

async function afterProjectSwitch() {
  await Promise.all([
    loadDashboard(),
    loadDestinations(),
  ]);
  document.getElementById('settings-api-key').value = currentProject?.api_key || '';
  const ingestEl = document.getElementById('settings-ingest-url');
  if (ingestEl) ingestEl.value = `${window.location.origin}/api/v1/ingest`;
}

function openNewProjectModal() {
  document.getElementById('ps-dropdown').classList.remove('open');
  openModal('modal-new-project');
}

async function createProject() {
  const name = document.getElementById('new-project-name').value.trim();
  if (!name) return;
  try {
    const p = await apiFetch('/projects', { method: 'POST', body: JSON.stringify({ name }) });
    projects.push(p);
    currentProject = p;
    renderProjectSwitcher();
    closeModal('modal-new-project');
    await afterProjectSwitch();
    toast('Project created', 'success');
  } catch (e) { toast(e.message, 'error'); }
}

// ---------------------------------------------------------------------------
// WebSocket
// ---------------------------------------------------------------------------
function connectWS() {
  if (!currentProject) return;
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${proto}//${location.host}/ws/${currentProject.api_key}`;

  ws = new WebSocket(url);
  setWsDot('connecting');

  ws.onopen = () => {
    setWsDot('connected');
    if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }
  };

  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === 'webhook.updated') {
        updateWebhookRowLive(msg.data);
        // Refresh dashboard KPIs if overview is active
        if (document.getElementById('page-overview')?.classList.contains('active')) {
          loadDashboard();
        }
      }
    } catch {}
  };

  ws.onclose = () => {
    setWsDot('disconnected');
    wsReconnectTimer = setTimeout(connectWS, 3000);
  };

  ws.onerror = () => ws.close();
}

function reconnectWS() {
  if (ws) ws.close();
  setTimeout(connectWS, 100);
}

function setWsDot(state) {
  const dot = document.getElementById('ws-dot');
  const txt = document.getElementById('ws-status-text');
  dot.className = 'ws-dot ' + state;
  txt.textContent = state === 'connected' ? 'Live' : state === 'connecting' ? 'Connecting…' : 'Disconnected';
}

function updateWebhookRowLive(data) {
  const row = document.querySelector(`tr[data-id="${data.id}"]`);
  if (row) {
    const statusCell = row.querySelector('.badge');
    if (statusCell) {
      statusCell.className = `badge ${data.status}`;
      statusCell.textContent = data.status;
    }
    loadStats();
  }
}

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------
function navTo(page) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  const pageEl = document.getElementById('page-' + page);
  const navEl = document.querySelector(`.nav-item[data-page="${page}"]`);
  if (pageEl) pageEl.classList.add('active');
  if (navEl) navEl.classList.add('active');

  // Lazy-load page data
  if (page === 'overview') { loadDashboard(); }
  if (page === 'webhooks') loadWebhooks(1);
  if (page === 'destinations') loadDestinations();
  if (page === 'alerts') loadAlerts();
  if (page === 'team') loadTeam();
  if (page === 'event-types') loadEventTypes();
  if (page === 'simulate') initSimulator();
  if (page === 'replay') initReplay();
  if (page === 'analytics') loadAnalytics();
  if (page === 'ai') { /* static UI, no load needed */ }
  if (page === 'dlq-intelligence') { loadDLQIntelligence(); }
}

// Close dropdowns on outside click
document.addEventListener('click', () => {
  document.getElementById('ps-dropdown').classList.remove('open');
  document.getElementById('user-dropdown').classList.remove('open');
});

// ---------------------------------------------------------------------------
// Stats
// ---------------------------------------------------------------------------
async function loadStats() {
  try {
    const s = await apiFetch('/stats');
    const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    set('stat-total',      s.total_webhooks);
    set('stat-pending',    s.pending_count);
    set('stat-processing', s.processing_count);
    set('stat-completed',  s.completed_count);
    set('stat-failed',     s.failed_count);
    set('stat-rate',       s.success_rate + '%');

    const badge = document.getElementById('failed-badge');
    if (s.failed_count > 0) {
      badge.textContent = s.failed_count;
      badge.classList.remove('hidden');
    } else {
      badge.classList.add('hidden');
    }
  } catch {}
}

// ---------------------------------------------------------------------------
// Dashboard Overview — single /api/v1/dashboard call
// ---------------------------------------------------------------------------
async function loadDashboard() {
  try {
    const d = await apiFetch('/dashboard');
    _renderHealthBanner(d.system_status);
    _renderKPIs(d.kpis);
    _renderIncidents(d.active_incidents || []);
    _renderSparkline(d.sparkline || []);
    _renderRecentFailures(d.recent_failures || []);
    document.getElementById('health-updated').textContent = 'Updated ' + relTime(new Date().toISOString());
  } catch (e) {
    // Silently degrade — don't break the page on a transient error
    document.getElementById('health-banner-title').textContent = 'Unable to load health data';
    document.getElementById('health-banner-sub').textContent = e.message;
  }
}

function _renderHealthBanner(status) {
  const banner = document.getElementById('health-banner');
  const title = document.getElementById('health-banner-title');
  const sub = document.getElementById('health-banner-sub');
  const dot = document.getElementById('health-dot-large');

  const cfg = {
    healthy:  { cls: 'healthy',  text: 'System Healthy',  sub: 'All destinations delivering normally' },
    degraded: { cls: 'degraded', text: 'System Degraded', sub: 'Some destinations experiencing failures' },
    critical: { cls: 'critical', text: 'System Critical', sub: 'Immediate attention required' },
  };
  const c = cfg[status] || cfg.healthy;
  banner.className = 'health-banner ' + c.cls;
  title.textContent = c.text;
  sub.textContent = c.sub;
}

function _renderKPIs(kpis) {
  const rate = kpis.success_rate_24h ?? 100;
  const rateEl = document.getElementById('kpi-success-rate');
  rateEl.textContent = rate.toFixed(1) + '%';
  rateEl.className = 'kpi-value ' + (rate >= 99 ? 'kpi-good' : rate >= 95 ? 'kpi-warn' : 'kpi-bad');

  const p95 = kpis.p95_latency_ms ?? 0;
  document.getElementById('kpi-p95').textContent = p95 ? p95 + ' ms' : '—';

  const dlq = kpis.dlq_depth ?? 0;
  const dlqEl = document.getElementById('kpi-dlq');
  const dlqCard = document.getElementById('kpi-dlq-card');
  dlqEl.textContent = dlq.toLocaleString();
  dlqEl.className = 'kpi-value ' + (dlq === 0 ? 'kpi-good' : dlq < 10 ? 'kpi-warn' : 'kpi-bad');
  dlqCard.classList.toggle('kpi-card--alert', dlq > 0);

  const cb = kpis.circuit_breakers || {};
  const open = (cb.open || 0) + (cb.half_open || 0);
  const total = (cb.closed || 0) + open;
  const circuitEl = document.getElementById('kpi-circuit');
  const circuitCard = document.getElementById('kpi-circuit-card');
  circuitEl.textContent = total > 0 ? (total - open) + ' / ' + total : '—';
  circuitEl.className = 'kpi-value ' + (open === 0 ? 'kpi-good' : 'kpi-bad');
  circuitCard.classList.toggle('kpi-card--alert', open > 0);
  const circuitSub = document.getElementById('kpi-circuit-sub');
  if (open > 0 && kpis.open_destinations?.length) {
    circuitSub.textContent = 'Open: ' + kpis.open_destinations.slice(0, 2).join(', ');
    circuitSub.style.color = 'var(--danger)';
  } else {
    circuitSub.textContent = 'destinations healthy';
    circuitSub.style.color = '';
  }
}

function _renderIncidents(incidents) {
  const section = document.getElementById('incidents-section');
  const list = document.getElementById('incidents-list');
  if (!incidents.length) { section.style.display = 'none'; return; }
  section.style.display = '';
  const severityColor = { critical: 'var(--danger)', high: 'var(--warn)', medium: 'var(--info)', low: 'var(--text3)' };
  list.innerHTML = incidents.map(i => `
    <div class="incident-row">
      <div class="incident-row-left">
        <span class="incident-sev-dot" style="background:${severityColor[i.severity] || 'var(--text3)'}"></span>
        <div>
          <div class="incident-row-title">${esc(i.category || 'Unknown')}${i.subcategory ? ' · ' + esc(i.subcategory) : ''}</div>
          <div class="incident-row-meta">${esc(i.destination_name || 'Unknown destination')} · ${i.affected_count} affected · ${relTime(i.last_seen_at)}</div>
        </div>
      </div>
      <div class="incident-row-right">
        <span class="badge ${i.state === 'OPEN' ? 'failed' : 'pending'}">${i.state}</span>
        ${i.recommended_action ? `<div class="incident-row-rec">${esc(i.recommended_action.substring(0, 80))}${i.recommended_action.length > 80 ? '…' : ''}</div>` : ''}
      </div>
    </div>
  `).join('');
}

function _renderSparkline(data) {
  const container = document.getElementById('throughput-chart');
  if (!data.length) {
    container.innerHTML = '<div class="sparkline-empty">No delivery data in the last 24 hours</div>';
    return;
  }
  const maxVal = Math.max(...data.map(d => d.delivered + d.failed), 1);
  const bars = data.map(d => {
    const total = d.delivered + d.failed;
    const delivH = Math.max(4, Math.round((d.delivered / maxVal) * 80));
    const failH = d.failed > 0 ? Math.max(2, Math.round((d.failed / maxVal) * 80)) : 0;
    const h = new Date(d.hour);
    const label = h.getHours().toString().padStart(2, '0') + ':00';
    return `
      <div class="spark-bar-wrap" title="${label} · ${d.delivered} delivered, ${d.failed} failed">
        <div class="spark-bar-stack">
          ${failH > 0 ? `<div class="spark-seg failed" style="height:${failH}px"></div>` : ''}
          <div class="spark-seg delivered" style="height:${delivH}px"></div>
        </div>
        <div class="spark-label">${h.getHours() % 6 === 0 ? label : ''}</div>
      </div>`;
  }).join('');
  container.innerHTML = `<div class="spark-bars">${bars}</div>`;
}

function _renderRecentFailures(failures) {
  const el = document.getElementById('recent-failures-list');
  if (!failures.length) {
    el.innerHTML = `
      <div class="empty-state-sm">
        <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><path d="M8 12h8"/></svg>
        <div>No recent failures</div>
      </div>`;
    return;
  }
  const catColor = {
    TIMEOUT: 'var(--warn)', NETWORK: 'var(--warn)', SERVER_ERROR: 'var(--info)',
    AUTHENTICATION: 'var(--danger)', AUTHORIZATION: 'var(--danger)',
    RATE_LIMITING: 'var(--accent-light)', CLIENT_ERROR: 'var(--text2)',
  };
  el.innerHTML = failures.map(f => {
    const cat = f.failure_category || 'UNKNOWN';
    const color = catColor[cat] || 'var(--text3)';
    const name = f.destination_name || _shortUrl(f.destination_url);
    return `
      <div class="failure-row">
        <div class="failure-row-left">
          <span class="failure-cat-badge" style="color:${color};border-color:${color}">${cat.replace('_', ' ')}</span>
          <div>
            <div class="failure-row-dest">${esc(name)}</div>
            <div class="failure-row-err">${esc((f.error_message || '').substring(0, 72))}${(f.error_message || '').length > 72 ? '…' : ''}</div>
          </div>
        </div>
        <div class="failure-row-right">
          <span class="failure-time">${relTime(f.updated_at)}</span>
          <button class="btn btn-xs btn-secondary" onclick="replayFailure('${f.id}')">Replay</button>
        </div>
      </div>`;
  }).join('');
}

async function replayFailure(id) {
  try {
    await apiFetch('/webhooks/' + id + '/replay', { method: 'POST' });
    toast('Rescheduled for delivery', 'success');
    loadDashboard();
  } catch (e) { toast(e.message, 'error'); }
}

function _shortUrl(url) {
  try { const u = new URL(url); return u.hostname; } catch { return url || '—'; }
}

// ---------------------------------------------------------------------------
// Webhooks table
// ---------------------------------------------------------------------------
async function loadWebhooks(page = 1) {
  currentPage = page;
  const status = document.getElementById('wh-status-filter')?.value || '';
  const search = document.getElementById('wh-search')?.value || '';
  const destId = document.getElementById('wh-dest-filter')?.value || '';

  let qs = `?page=${page}&limit=25`;
  if (status) qs += `&status=${status}`;
  if (search) qs += `&search=${encodeURIComponent(search)}`;
  if (destId) qs += `&destination_id=${destId}`;

  try {
    const data = await apiFetch('/webhooks' + qs);
    renderWebhooksTable(data, document.getElementById('webhooks-table-body'));
    renderPagination('wh-pagination', data.page, data.total_pages, loadWebhooks);
  } catch (e) { toast(e.message, 'error'); }
}

async function loadOverviewTable() {
  try {
    const data = await apiFetch('/webhooks?page=1&limit=10');
    const wrap = document.getElementById('overview-table-wrap');
    if (!wrap) return;
    renderWebhooksTable(data, wrap, true);
  } catch {}
}

function renderWebhooksTable(data, container, compact = false) {
  if (!data.webhooks?.length) {
    container.innerHTML = `<div class="empty-state"><div class="icon">📭</div><div class="title">No webhooks</div></div>`;
    return;
  }

  const rows = data.webhooks.map(w => `
    <tr data-id="${w.id}" onclick="openPanel('${w.id}')" class="${selectedWebhookId === w.id ? 'selected' : ''}">
      <td class="cell-mono">${w.id.substring(0, 8)}…</td>
      <td class="cell-url">${esc(w.destination_url)}</td>
      <td><span class="badge ${w.status}">${w.status}</span></td>
      <td>${w.retry_count}/${w.max_retries}</td>
      ${!compact ? `<td class="cell-mono" style="max-width:120px;overflow:hidden;text-overflow:ellipsis">${esc(w.event_id?.substring(0,20) || '—')}</td>` : ''}
      <td class="cell-time">${relTime(w.created_at)}</td>
    </tr>
  `).join('');

  container.innerHTML = `
    <table>
      <thead><tr>
        <th>ID</th>
        <th>Destination</th>
        <th>Status</th>
        <th>Retries</th>
        ${!compact ? '<th>Event ID</th>' : ''}
        <th>Created</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function debounceSearch() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadWebhooks(1), 400);
}

// ---------------------------------------------------------------------------
// Detail panel
// ---------------------------------------------------------------------------
async function openPanel(id) {
  selectedWebhookId = id;
  document.querySelectorAll('tbody tr').forEach(r => r.classList.toggle('selected', r.dataset.id === id));
  const panel = document.getElementById('detail-panel');
  panel.classList.remove('hidden');

  try {
    const w = await apiFetch('/webhooks/' + id);
    document.getElementById('panel-webhook-id').textContent = 'Webhook ' + id.substring(0, 8) + '…';
    document.getElementById('panel-created-at').textContent = new Date(w.created_at).toLocaleString();
    document.getElementById('panel-status-badge').innerHTML = `<span class="badge ${w.status}">${w.status}</span>`;
    document.getElementById('panel-dest-url').textContent = w.destination_url;
    document.getElementById('panel-event-id').textContent = w.event_id || '—';
    document.getElementById('panel-tenant-id').textContent = w.tenant_id;
    document.getElementById('panel-retries').textContent = `${w.retry_count} / ${w.max_retries}`;
    document.getElementById('panel-idempotency').textContent = w.idempotency_key || '—';
    document.getElementById('panel-ordering-key').textContent = w.ordering_key || '—';
    document.getElementById('panel-payload').textContent = JSON.stringify(w.payload, null, 2);
    document.getElementById('panel-headers').textContent = JSON.stringify(w.headers, null, 2);

    const replay = document.getElementById('panel-replay-btn');
    replay.style.display = w.status === 'failed' ? 'inline-flex' : 'none';

    // Timeline
    const timeline = document.getElementById('panel-timeline');
    if (!w.attempts?.length) {
      timeline.innerHTML = '<div style="font-size:12px;color:var(--text3)">No attempts yet</div>';
    } else {
      timeline.innerHTML = w.attempts.map(a => {
        const ok = a.status_code >= 200 && a.status_code < 300;
        const cls = ok ? 'success' : 'failed';
        return `
          <div class="timeline-item">
            <div class="timeline-dot ${cls}">${a.attempt_number}</div>
            <div class="timeline-content">
              <div class="timeline-title">
                ${ok ? '✅' : '❌'} Attempt ${a.attempt_number}
                ${a.status_code ? `<span class="inline-code">${a.status_code}</span>` : ''}
                ${a.duration_ms ? `<span style="color:var(--text3);font-size:11px">${a.duration_ms}ms</span>` : ''}
              </div>
              <div class="timeline-meta">${a.attempted_at ? relTime(a.attempted_at) : ''}${a.retry_strategy_used ? ` · ${a.retry_strategy_used}` : ''}</div>
              ${a.error_message ? `<div class="timeline-error">${esc(a.error_message)}</div>` : ''}
            </div>
          </div>
        `;
      }).join('');
    }
  } catch (e) { toast(e.message, 'error'); }
}

function closePanel() {
  selectedWebhookId = null;
  document.getElementById('detail-panel').classList.add('hidden');
  document.querySelectorAll('tbody tr.selected').forEach(r => r.classList.remove('selected'));
}

async function replaySelected() {
  if (!selectedWebhookId) return;
  try {
    await apiFetch('/webhooks/' + selectedWebhookId + '/replay', { method: 'POST' });
    toast('Webhook rescheduled for replay', 'success');
    openPanel(selectedWebhookId);
    loadWebhooks(currentPage);
    loadStats();
  } catch (e) { toast(e.message, 'error'); }
}

// ---------------------------------------------------------------------------
// Destinations
// ---------------------------------------------------------------------------
async function loadDestinations() {
  try {
    destinations = await apiFetch('/destinations');
    renderDestTable();
    populateDestSelects();
  } catch {}
}

function renderDestTable() {
  const tbody = document.getElementById('dest-tbody');
  const banner = document.getElementById('cb-open-banner');
  if (!tbody) return;

  const openCount = destinations.filter(d => d.circuit_state === 'open').length;
  if (banner) {
    if (openCount > 0) {
      banner.classList.remove('hidden');
      document.getElementById('cb-open-banner-text').textContent =
        `${openCount} destination circuit breaker${openCount > 1 ? 's are' : ' is'} OPEN — deliveries suspended.`;
    } else {
      banner.classList.add('hidden');
    }
  }

  if (!destinations.length) {
    tbody.innerHTML = `<tr><td colspan="6"><div class="empty-state">
      <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.25"><circle cx="12" cy="12" r="3"/><circle cx="12" cy="12" r="9"/></svg>
      <div class="title">No destinations yet</div>
      <div class="desc">Add a destination to start routing webhooks to your endpoints.</div>
    </div></td></tr>`;
    return;
  }

  const circuitClass = s => s === 'closed' ? 'CLOSED' : s === 'half_open' ? 'HALF_OPEN' : 'OPEN';
  const statusClass  = d => {
    if (d.circuit_state === 'open') return 'unhealthy';
    if (d.circuit_state === 'half_open') return 'degraded';
    return 'healthy';
  };
  const statusLabel  = d => {
    if (!d.is_enabled) return 'Disabled';
    if (d.circuit_state === 'open') return 'Open';
    if (d.circuit_state === 'half_open') return 'Recovering';
    return 'Healthy';
  };

  tbody.innerHTML = destinations.map(d => `
    <tr>
      <td><span class="dest-status-dot ${statusClass(d)}">${statusLabel(d)}</span></td>
      <td style="font-weight:500;color:var(--text)">${esc(d.name)}</td>
      <td class="cell-url" title="${esc(d.url)}">${esc(d.url)}</td>
      <td style="font-family:var(--mono);font-size:12px">0 / ${d.max_retries}</td>
      <td><span class="circuit-text ${circuitClass(d.circuit_state)}">${circuitClass(d.circuit_state).replace('_', '‑')}</span></td>
      <td>
        <div class="dest-row-actions">
          <button class="btn btn-xs btn-secondary" onclick="viewDestSla('${d.id}','${esc(d.name)}')">SLA</button>
          <button class="btn btn-xs btn-secondary" onclick="openDestModal(destinations.find(x=>x.id==='${d.id}'))">Edit</button>
          <button class="btn btn-xs btn-danger" onclick="deleteDestination('${d.id}')">Delete</button>
        </div>
      </td>
    </tr>
  `).join('');
}

function populateDestSelects() {
  const selects = ['wh-dest-filter', 'sim-destination', 'replay-dest'];
  selects.forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    const blank = el.querySelector('option[value=""]')?.outerHTML || '<option value="">All</option>';
    el.innerHTML = blank + destinations.map(d => `<option value="${d.id}">${esc(d.name)}</option>`).join('');
  });
}

function openDestModal(existing = null) {
  document.getElementById('dest-name').value = existing?.name || '';
  document.getElementById('dest-url').value = existing?.url || '';
  document.getElementById('dest-max-retries').value = existing?.max_retries ?? 5;
  document.getElementById('dest-backoff').value = existing?.backoff_base_seconds ?? 30;
  document.getElementById('dest-filter').value = existing?.filter_expression || '';
  document.getElementById('dest-ordering-key').value = existing?.ordering_key_field || '';
  document.getElementById('dest-secret').value = '';
  document.getElementById('dest-transform-type').value = existing?.transform_type || 'none';
  document.getElementById('dest-transform-map').value = existing?.transform_map ? JSON.stringify(existing.transform_map, null, 2) : '';
  document.getElementById('dest-transform-code').value = existing?.transform_code || '';
  document.getElementById('dest-modal-title').textContent = existing ? 'Edit Destination' : 'New Destination';
  toggleTransformEditor();
  openModal('modal-dest');
}

function toggleTransformEditor() {
  const type = document.getElementById('dest-transform-type').value;
  document.getElementById('dest-transform-json-wrap').style.display = type === 'json_map' ? '' : 'none';
  document.getElementById('dest-transform-js-wrap').style.display = type === 'javascript' ? '' : 'none';
}

async function saveDestination() {
  const transformType = document.getElementById('dest-transform-type').value;
  let transformMap = null;
  let transformCode = null;
  if (transformType === 'json_map') {
    const raw = document.getElementById('dest-transform-map').value.trim();
    if (raw) { try { transformMap = JSON.parse(raw); } catch { toast('Field Map must be valid JSON', 'error'); return; } }
  } else if (transformType === 'javascript') {
    transformCode = document.getElementById('dest-transform-code').value.trim() || null;
  }

  const body = {
    name: document.getElementById('dest-name').value.trim(),
    url: document.getElementById('dest-url').value.trim(),
    max_retries: parseInt(document.getElementById('dest-max-retries').value),
    backoff_base_seconds: parseInt(document.getElementById('dest-backoff').value),
    filter_expression: document.getElementById('dest-filter').value.trim() || null,
    ordering_key_field: document.getElementById('dest-ordering-key').value.trim() || null,
    webhook_secret: document.getElementById('dest-secret').value.trim() || null,
    transform_type: transformType,
    transform_map: transformMap,
    transform_code: transformCode,
  };
  if (!body.name || !body.url) { toast('Name and URL are required', 'error'); return; }
  try {
    await apiFetch('/destinations', { method: 'POST', body: JSON.stringify(body) });
    closeModal('modal-dest');
    toast('Destination created', 'success');
    await loadDestinations();
  } catch (e) { toast(e.message, 'error'); }
}

async function testDestination(id) {
  try {
    const r = await apiFetch(`/destinations/${id}/test`, { method: 'POST' });
    toast(r.success ? `✅ ${r.status_code} — Test passed` : `❌ Test failed: ${r.error || r.status_code}`, r.success ? 'success' : 'error');
  } catch (e) { toast(e.message, 'error'); }
}

async function deleteDestination(id) {
  if (!confirm('Delete this destination?')) return;
  try {
    await apiFetch(`/destinations/${id}`, { method: 'DELETE' });
    toast('Destination deleted', 'success');
    await loadDestinations();
  } catch (e) { toast(e.message, 'error'); }
}

// ---------------------------------------------------------------------------
// Alerts
// ---------------------------------------------------------------------------
async function loadAlerts() {
  try {
    const alerts = await apiFetch('/alerts');
    const el = document.getElementById('alerts-list');
    if (!el) return;
    if (!alerts.length) {
      el.innerHTML = `<div class="empty-state"><div class="icon">🔔</div><div class="title">No alert channels</div><div class="desc">Add Slack or email to get notified on DLQ events</div></div>`;
      return;
    }
    el.innerHTML = alerts.map(a => `
      <div class="alert-row">
        <div class="alert-icon">${a.channel_type === 'slack' ? '💬' : '📧'}</div>
        <div class="alert-info">
          <div class="alert-name">${esc(a.name)}</div>
          <div class="alert-type">${a.channel_type} · ${a.enabled ? 'Enabled' : 'Disabled'}</div>
        </div>
        <div class="alert-actions">
          <button class="btn btn-xs btn-secondary" onclick="testAlert('${a.id}')">Test</button>
          <button class="btn btn-xs btn-danger" onclick="deleteAlert('${a.id}')">Delete</button>
        </div>
      </div>
    `).join('');
  } catch {}
}

function openAlertModal() {
  document.getElementById('alert-name').value = '';
  document.getElementById('alert-slack-url').value = '';
  toggleAlertFields();
  openModal('modal-alert');
}

function toggleAlertFields() {
  const type = document.getElementById('alert-type').value;
  document.getElementById('alert-slack-fields').style.display = type === 'slack' ? '' : 'none';
  document.getElementById('alert-email-fields').style.display = type === 'email' ? '' : 'none';
}

async function saveAlert() {
  const type = document.getElementById('alert-type').value;
  let config = {};
  if (type === 'slack') config = { webhook_url: document.getElementById('alert-slack-url').value };
  else config = {
    smtp_host: document.getElementById('alert-smtp-host').value,
    smtp_port: parseInt(document.getElementById('alert-smtp-port').value),
    username: document.getElementById('alert-smtp-user').value,
    password: document.getElementById('alert-smtp-pass').value,
    from: document.getElementById('alert-smtp-from').value,
    to: document.getElementById('alert-smtp-to').value,
  };
  const body = { name: document.getElementById('alert-name').value, channel_type: type, config };
  try {
    await apiFetch('/alerts', { method: 'POST', body: JSON.stringify(body) });
    closeModal('modal-alert');
    toast('Alert created', 'success');
    loadAlerts();
  } catch (e) { toast(e.message, 'error'); }
}

async function testAlert(id) {
  try {
    const r = await apiFetch(`/alerts/${id}/test`, { method: 'POST' });
    toast(r.message, 'success');
  } catch (e) { toast(e.message, 'error'); }
}

async function deleteAlert(id) {
  if (!confirm('Delete this alert?')) return;
  try {
    await apiFetch(`/alerts/${id}`, { method: 'DELETE' });
    toast('Alert deleted', 'success');
    loadAlerts();
  } catch (e) { toast(e.message, 'error'); }
}

// ---------------------------------------------------------------------------
// Event Types
// ---------------------------------------------------------------------------
async function loadEventTypes() {
  try {
    const types = await apiFetch('/event-types');
    const el = document.getElementById('event-types-list');
    if (!el) return;
    if (!types.length) {
      el.innerHTML = `<div class="empty-state"><div class="icon">📋</div><div class="title">No event types</div><div class="desc">Register event types to document your webhook schema</div></div>`;
      return;
    }
    el.innerHTML = `<div class="table-wrap"><table>
      <thead><tr><th>Name</th><th>Version</th><th>Description</th><th>Actions</th></tr></thead>
      <tbody>${types.map(t => `
        <tr>
          <td class="cell-mono">${esc(t.name)}</td>
          <td><span class="inline-code">v${t.version}</span></td>
          <td style="color:var(--text2)">${esc(t.description || '—')}</td>
          <td><button class="btn btn-xs btn-danger" onclick="deleteEventType('${t.id}')">Delete</button></td>
        </tr>
      `).join('')}</tbody>
    </table></div>`;
  } catch {}
}

function openEventTypeModal() { openModal('modal-event-type'); }

async function saveEventType() {
  const body = {
    name: document.getElementById('et-name').value.trim(),
    description: document.getElementById('et-desc').value.trim(),
    version: document.getElementById('et-version').value.trim() || '1',
    example_payload: (() => { try { return JSON.parse(document.getElementById('et-example').value); } catch { return null; } })(),
  };
  if (!body.name) { toast('Name is required', 'error'); return; }
  try {
    await apiFetch('/event-types', { method: 'POST', body: JSON.stringify(body) });
    closeModal('modal-event-type');
    toast('Event type created', 'success');
    loadEventTypes();
  } catch (e) { toast(e.message, 'error'); }
}

async function deleteEventType(id) {
  if (!confirm('Delete this event type?')) return;
  try {
    await apiFetch(`/event-types/${id}`, { method: 'DELETE' });
    toast('Deleted', 'success');
    loadEventTypes();
  } catch (e) { toast(e.message, 'error'); }
}

// ---------------------------------------------------------------------------
// Simulator
// ---------------------------------------------------------------------------
async function initSimulator() {
  await loadDestinations();
  loadSimEventTypes();
  populateDestSelects();
}

async function loadSimEventTypes() {
  try {
    const providers = await apiFetch('/simulate/providers');
    const provider = document.getElementById('sim-provider')?.value || 'stripe';
    const sel = document.getElementById('sim-event-type');
    if (!sel) return;
    const events = providers[provider] || [];
    sel.innerHTML = events.map(e => `<option value="${e}">${e}</option>`).join('');
  } catch {}
}

async function fireSimulation() {
  const provider = document.getElementById('sim-provider').value;
  const event_type = document.getElementById('sim-event-type').value;
  const destination_id = document.getElementById('sim-destination').value;
  if (!destination_id) { toast('Select a destination', 'error'); return; }
  try {
    const r = await apiFetch('/simulate', {
      method: 'POST',
      body: JSON.stringify({ provider, event_type, destination_id }),
    });
    const el = document.getElementById('sim-result');
    el.innerHTML = `
      <div style="color:var(--success);font-weight:500;margin-bottom:12px">✅ Fired! Webhook ID: <span class="inline-code">${r.webhook_id.substring(0,8)}…</span></div>
      <div style="font-size:12px;color:var(--text3);margin-bottom:8px">Payload sent:</div>
      <div class="json-view">${JSON.stringify(r.payload, null, 2)}</div>
    `;
    toast('Simulation fired', 'success');
    loadWebhooks(1);
  } catch (e) { toast(e.message, 'error'); }
}

// ---------------------------------------------------------------------------
// Bulk Replay
// ---------------------------------------------------------------------------
function initReplay() {
  populateDestSelects();
  // Set sensible defaults: last 1 hour
  const now = new Date();
  const oneHourAgo = new Date(now - 3600000);
  document.getElementById('replay-from').value = toLocalDatetimeString(oneHourAgo);
  document.getElementById('replay-to').value = toLocalDatetimeString(now);
}

async function startBulkReplay() {
  const from = document.getElementById('replay-from').value;
  const to = document.getElementById('replay-to').value;
  const destId = document.getElementById('replay-dest').value;
  const rate = parseInt(document.getElementById('replay-rate').value) || 100;
  if (!from || !to) { toast('Select a time window', 'error'); return; }
  try {
    const r = await apiFetch('/webhooks/replay-window', {
      method: 'POST',
      body: JSON.stringify({
        from_time: new Date(from).toISOString(),
        to_time: new Date(to).toISOString(),
        destination_id: destId || null,
        replay_rate_per_minute: rate,
      }),
    });
    toast(`Replay job started: ${r.total_count} webhooks (~${r.estimated_duration_minutes} min)`, 'info');
    document.getElementById('replay-jobs-list').innerHTML = `
      <div style="background:var(--surface2);border:1px solid var(--border);border-radius:var(--radius);padding:14px">
        <div style="font-weight:500;margin-bottom:4px">Job <span class="inline-code">${r.job_id.substring(0,8)}…</span></div>
        <div style="font-size:12px;color:var(--text3)">${r.total_count} webhooks · est. ${r.estimated_duration_minutes} min · ${rate} evt/min</div>
      </div>
    `;
  } catch (e) { toast(e.message, 'error'); }
}

// ---------------------------------------------------------------------------
// Team
// ---------------------------------------------------------------------------
async function loadTeam() {
  if (!currentProject) return;
  try {
    const members = await apiFetch(`/projects/${currentProject.id}/members`);
    const el = document.getElementById('team-list');
    if (!el) return;
    el.innerHTML = `<table>
      <thead><tr><th>Email</th><th>Role</th><th>Joined</th><th>Actions</th></tr></thead>
      <tbody>${members.map(m => `
        <tr>
          <td>${esc(m.email || '—')}</td>
          <td><span class="badge ${m.role === 'owner' ? 'completed' : 'pending'}">${m.role}</span></td>
          <td class="cell-time">${relTime(m.created_at)}</td>
          <td>${m.user_id !== currentUser?.id ? `<button class="btn btn-xs btn-danger" onclick="removeMember('${m.user_id}')">Remove</button>` : ''}</td>
        </tr>
      `).join('')}</tbody>
    </table>`;
  } catch {}
}

function openInviteModal() { openModal('modal-invite'); }

async function inviteMember() {
  const email = document.getElementById('invite-email').value.trim();
  const role = document.getElementById('invite-role').value;
  if (!email) { toast('Enter an email', 'error'); return; }
  try {
    await apiFetch(`/projects/${currentProject.id}/members`, {
      method: 'POST',
      body: JSON.stringify({ email, role }),
    });
    closeModal('modal-invite');
    toast('Member invited', 'success');
    loadTeam();
  } catch (e) { toast(e.message, 'error'); }
}

async function removeMember(userId) {
  if (!confirm('Remove this member?')) return;
  try {
    await apiFetch(`/projects/${currentProject.id}/members/${userId}`, { method: 'DELETE' });
    toast('Member removed', 'success');
    loadTeam();
  } catch (e) { toast(e.message, 'error'); }
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------
function copyApiKey() {
  const key = document.getElementById('settings-api-key').value;
  navigator.clipboard.writeText(key).then(() => toast('API key copied', 'success'));
}

async function rotateApiKey() {
  if (!currentProject) return;
  const confirmed = window.confirm(
    'Rotate API key?\n\nThe current key will stop working immediately. ' +
    'You must update every service and SDK that uses it before rotating.'
  );
  if (!confirmed) return;
  const btn = document.getElementById('rotate-key-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Rotating…'; }
  try {
    const data = await apiFetch(`/projects/${currentProject.id}/rotate-key`, { method: 'POST' });
    currentProject.api_key = data.api_key;
    document.getElementById('settings-api-key').value = data.api_key;
    toast('API key rotated — copy and update all services now', 'success');
  } catch (e) {
    toast('Rotation failed: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Rotate'; }
  }
}

function copyIngestUrl() {
  const url = document.getElementById('settings-ingest-url').value;
  if (!url) return;
  navigator.clipboard.writeText(url).then(() => toast('Ingestion URL copied', 'success'));
}

function confirmDeleteProject() {
  if (!currentProject) return;
  const name = currentProject.name;
  const confirmed = window.confirm(`Delete project "${name}"?\n\nThis will permanently remove all webhooks, destinations, and data. This action cannot be undone.`);
  if (!confirmed) return;
  apiFetch(`/projects/${currentProject.id}`, { method: 'DELETE' })
    .then(() => { toast('Project deleted', 'success'); return loadProjects(); })
    .catch(e => toast('Failed to delete: ' + e.message, 'error'));
}

// ---------------------------------------------------------------------------
// AI tab switcher
// ---------------------------------------------------------------------------
function switchAiTab(name) {
  ['analyze', 'filter', 'transform'].forEach(t => {
    const panel = document.getElementById(`ai-tab-${t}`);
    const tab   = document.querySelector(`.tab[data-tab="${t}"]`);
    if (!panel || !tab) return;
    const active = t === name;
    panel.style.display = active ? 'grid' : 'none';
    tab.classList.toggle('active', active);
  });
}

// ---------------------------------------------------------------------------
// Modal helpers
// ---------------------------------------------------------------------------
function openModal(id) { document.getElementById(id).classList.add('open'); }
function closeModal(id) { document.getElementById(id).classList.remove('open'); }

// Close modal on overlay click
document.querySelectorAll('.modal-overlay').forEach(overlay => {
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.classList.remove('open'); });
});

// ---------------------------------------------------------------------------
// Pagination
// ---------------------------------------------------------------------------
function renderPagination(containerId, page, totalPages, loadFn) {
  const el = document.getElementById(containerId);
  if (!el || totalPages <= 1) { if (el) el.innerHTML = ''; return; }
  let html = `<button class="page-btn" ${page <= 1 ? 'disabled' : ''} onclick="${loadFn.name}(${page - 1})">‹</button>`;
  for (let i = Math.max(1, page - 2); i <= Math.min(totalPages, page + 2); i++) {
    html += `<button class="page-btn ${i === page ? 'active' : ''}" onclick="${loadFn.name}(${i})">${i}</button>`;
  }
  html += `<button class="page-btn" ${page >= totalPages ? 'disabled' : ''} onclick="${loadFn.name}(${page + 1})">›</button>`;
  html += `<span class="page-info">Page ${page} of ${totalPages}</span>`;
  el.innerHTML = html;
}

// ---------------------------------------------------------------------------
// Toast
// ---------------------------------------------------------------------------
function toast(msg, type = 'info') {
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.innerHTML = `<span>${type === 'success' ? '✅' : type === 'error' ? '❌' : 'ℹ️'}</span><span>${esc(msg)}</span>`;
  document.getElementById('toast-container').appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------
function esc(str) {
  if (!str) return '';
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function relTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  const diff = (Date.now() - d) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return d.toLocaleDateString();
}

function toLocalDatetimeString(date) {
  const pad = n => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

// ---------------------------------------------------------------------------
// Analytics / SLA
// ---------------------------------------------------------------------------
async function loadAnalytics() {
  const el = document.getElementById('analytics-content');
  if (!el) return;

  if (!destinations.length) {
    await loadDestinations();
  }

  if (!destinations.length) {
    el.innerHTML = `<div class="empty-state"><div class="icon">🎯</div><div class="title">No destinations yet</div><div class="desc">Create a destination to see SLA metrics</div></div>`;
    return;
  }

  el.innerHTML = `<div style="color:var(--text3);font-size:13px;padding:24px">Loading SLA data…</div>`;

  const results = await Promise.allSettled(
    destinations.map(d => apiFetch(`/destinations/${d.id}/stats`).then(s => ({ dest: d, stats: s })))
  );

  const rows = results
    .filter(r => r.status === 'fulfilled')
    .map(r => r.value);

  if (!rows.length) {
    el.innerHTML = `<div class="empty-state"><div class="icon">📊</div><div class="title">No delivery data yet</div><div class="desc">SLA metrics appear after the first webhook delivery attempt</div></div>`;
    return;
  }

  el.innerHTML = `
    <div class="card" style="overflow:hidden">
      <table>
        <thead>
          <tr>
            <th>Destination</th>
            <th>Total</th>
            <th>Success Rate</th>
            <th>P50</th>
            <th>P95</th>
            <th>P99</th>
            <th>Circuit</th>
          </tr>
        </thead>
        <tbody>
          ${rows.map(({ dest, stats }) => {
            const rate = stats.success_rate ?? 0;
            const rateColor = rate >= 99 ? 'var(--success)' : rate >= 95 ? 'var(--warn)' : 'var(--danger)';
            const circuitColor = dest.circuit_state === 'closed' ? 'var(--success)' : dest.circuit_state === 'half_open' ? 'var(--warn)' : 'var(--danger)';
            return `
              <tr>
                <td>
                  <div style="font-weight:500">${esc(dest.name)}</div>
                  <div style="font-size:11px;color:var(--text3)">${esc(dest.url)}</div>
                </td>
                <td>${stats.total_attempts ?? 0}</td>
                <td><span style="color:${rateColor};font-weight:600">${rate.toFixed(1)}%</span></td>
                <td class="cell-mono">${stats.latency?.p50_ms ?? '—'}ms</td>
                <td class="cell-mono">${stats.latency?.p95_ms ?? '—'}ms</td>
                <td class="cell-mono">${stats.latency?.p99_ms ?? '—'}ms</td>
                <td><span style="color:${circuitColor}">⬤</span> ${dest.circuit_state}</td>
              </tr>
            `;
          }).join('')}
        </tbody>
      </table>
    </div>
    <div style="font-size:12px;color:var(--text3);margin-top:12px">SLA data from last 7 days of delivery attempts.</div>
  `;
}

// ---------------------------------------------------------------------------
// Destination SLA modal
// ---------------------------------------------------------------------------
async function viewDestSla(id, name) {
  document.getElementById('sla-modal-title').textContent = `SLA — ${name}`;
  document.getElementById('sla-modal-body').innerHTML = `<div style="color:var(--text3);text-align:center;padding:32px">Loading…</div>`;
  openModal('modal-dest-sla');

  try {
    const s = await apiFetch(`/destinations/${id}/stats`);
    const rate = s.success_rate ?? 0;
    const rateColor = rate >= 99 ? 'var(--success)' : rate >= 95 ? 'var(--warn)' : 'var(--danger)';

    document.getElementById('sla-modal-body').innerHTML = `
      <div class="sla-grid">
        <div class="sla-card">
          <div class="sla-label">Total Attempts</div>
          <div class="sla-value">${s.total_attempts ?? 0}</div>
        </div>
        <div class="sla-card">
          <div class="sla-label">Success Rate</div>
          <div class="sla-value" style="color:${rateColor}">${rate.toFixed(1)}%</div>
        </div>
        <div class="sla-card">
          <div class="sla-label">P50 Latency</div>
          <div class="sla-value">${s.latency?.p50_ms ?? '—'}<span class="sla-unit">ms</span></div>
        </div>
        <div class="sla-card">
          <div class="sla-label">P95 Latency</div>
          <div class="sla-value">${s.latency?.p95_ms ?? '—'}<span class="sla-unit">ms</span></div>
        </div>
        <div class="sla-card">
          <div class="sla-label">P99 Latency</div>
          <div class="sla-value">${s.latency?.p99_ms ?? '—'}<span class="sla-unit">ms</span></div>
        </div>
        <div class="sla-card">
          <div class="sla-label">Avg Latency</div>
          <div class="sla-value">${s.latency?.avg_ms ?? '—'}<span class="sla-unit">ms</span></div>
        </div>
      </div>
      <div style="font-size:12px;color:var(--text3);margin-top:16px">Last 7 days · ${s.total_attempts ?? 0} total attempts</div>
    `;
  } catch (e) {
    document.getElementById('sla-modal-body').innerHTML = `<div style="color:var(--danger);padding:16px">${esc(e.message)}</div>`;
  }
}

// ---------------------------------------------------------------------------
// AI Tools
// ---------------------------------------------------------------------------
async function aiAnalyze() {
  const rawPayload = document.getElementById('ai-payload').value.trim();
  if (!rawPayload) { toast('Paste a payload first', 'error'); return; }

  let payload;
  try { payload = JSON.parse(rawPayload); } catch { toast('Payload must be valid JSON', 'error'); return; }

  const el = document.getElementById('ai-result');
  el.innerHTML = `<div style="color:var(--text3)">🤖 Analyzing with Claude…</div>`;

  try {
    const r = await apiFetch('/ai/analyze-payload', { method: 'POST', body: JSON.stringify({ payload }) });
    el.innerHTML = `
      <div class="ai-result-section">
        <div class="ai-chip provider">${esc(r.provider || 'unknown')}</div>
        <div class="ai-chip confidence">confidence: ${((r.confidence || 0) * 100).toFixed(0)}%</div>
        ${r.event_type ? `<div class="ai-chip event-type">${esc(r.event_type)}</div>` : ''}
      </div>
      ${r.schema_summary ? `<div class="ai-summary">${esc(r.schema_summary)}</div>` : ''}
      ${r.filter_suggestions?.length ? `
        <div class="ai-section-title">Suggested Filters</div>
        ${r.filter_suggestions.map(f => `
          <div class="ai-suggestion" onclick="copyToClipboard('${esc(f.expression)}')">
            <div class="ai-expression">${esc(f.expression)}</div>
            <div class="ai-desc">${esc(f.description)}</div>
          </div>
        `).join('')}
      ` : ''}
      ${r.field_mapping_suggestions?.length ? `
        <div class="ai-section-title">Suggested Field Mappings</div>
        <div class="json-view">${esc(JSON.stringify(
          Object.fromEntries(r.field_mapping_suggestions.map(m => [m.to, m.from])),
          null, 2
        ))}</div>
      ` : ''}
      ${r.key_fields?.length ? `
        <div class="ai-section-title">Key Fields</div>
        <div>${r.key_fields.map(f => `<span class="inline-code">${esc(f)}</span>`).join(' ')}</div>
      ` : ''}
    `;
  } catch (e) {
    el.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`;
    if (e.message.includes('403') || e.message.includes('disabled')) {
      el.innerHTML += `<div style="color:var(--text3);margin-top:8px;font-size:12px">Set <span class="inline-code">ENABLE_AI_FEATURES=true</span> and <span class="inline-code">ANTHROPIC_API_KEY</span> in your .env to enable AI features.</div>`;
    }
  }
}

async function aiSuggestFilter() {
  const rawPayload = document.getElementById('ai-filter-payload').value.trim();
  const description = document.getElementById('ai-filter-desc').value.trim();
  if (!rawPayload || !description) { toast('Paste a payload and describe your filter', 'error'); return; }

  let payload;
  try { payload = JSON.parse(rawPayload); } catch { toast('Payload must be valid JSON', 'error'); return; }

  const el = document.getElementById('ai-filter-result');
  el.innerHTML = `<div style="color:var(--text3);font-size:12px">Generating…</div>`;

  try {
    const r = await apiFetch('/ai/suggest-filter', { method: 'POST', body: JSON.stringify({ sample_payload: payload, description }) });
    el.innerHTML = `
      <div style="font-size:12px;color:var(--text3);margin-bottom:6px">Generated expression:</div>
      <div class="ai-suggestion" onclick="copyToClipboard('${esc(r.expression || r)}')">
        <div class="ai-expression">${esc(r.expression || r)}</div>
      </div>
    `;
  } catch (e) {
    el.innerHTML = `<div style="color:var(--danger);font-size:12px">${esc(e.message)}</div>`;
  }
}

async function aiSuggestTransform() {
  const rawPayload = document.getElementById('ai-transform-payload').value.trim();
  const description = document.getElementById('ai-transform-desc').value.trim();
  if (!rawPayload || !description) { toast('Paste a payload and describe the output shape', 'error'); return; }

  let payload;
  try { payload = JSON.parse(rawPayload); } catch { toast('Payload must be valid JSON', 'error'); return; }

  const el = document.getElementById('ai-transform-result');
  el.innerHTML = `<div style="color:var(--text3);font-size:12px">Generating…</div>`;

  try {
    const r = await apiFetch('/ai/suggest-transform', { method: 'POST', body: JSON.stringify({ sample_payload: payload, description }) });
    const code = r.transform_code || r;
    el.innerHTML = `
      <div style="font-size:12px;color:var(--text3);margin-bottom:6px">Generated transform:</div>
      <div class="json-view" style="cursor:pointer" onclick="copyToClipboard(${JSON.stringify(code)})" title="Click to copy">${esc(code)}</div>
      <div style="font-size:11px;color:var(--text3);margin-top:6px">Click to copy — paste into the JavaScript Transform field on a destination.</div>
    `;
  } catch (e) {
    el.innerHTML = `<div style="color:var(--danger);font-size:12px">${esc(e.message)}</div>`;
  }
}

function copyToClipboard(text) {
  navigator.clipboard.writeText(text).then(() => toast('Copied to clipboard', 'success'));
}

// ---------------------------------------------------------------------------
// DLQ Intelligence
// ---------------------------------------------------------------------------
async function loadDLQIntelligence() {
  console.log('Loading DLQ Intelligence...');
  const loadingEl = document.getElementById('dlq-loading');
  const errorEl = document.getElementById('dlq-error');
  const contentEl = document.getElementById('dlq-content');
  
  if (!loadingEl || !errorEl || !contentEl) {
    console.error('DLQ Intelligence elements not found');
    return;
  }
  
  loadingEl.style.display = 'block';
  errorEl.style.display = 'none';
  contentEl.style.display = 'none';
  
  let hasError = false;
  let errorMessages = [];
  
  // Load health data
  try {
    const healthResponse = await fetch('/api/v1/dlq/health', {
      credentials: 'include'
    });
    if (healthResponse.ok) {
      const healthData = await healthResponse.json();
      updateHealthScore(healthData);
    } else {
      errorMessages.push('Health data unavailable');
      hasError = true;
    }
  } catch (error) {
    console.error('Error loading health data:', error);
    errorMessages.push('Health data unavailable');
    hasError = true;
  }
  
  // Load incident summary
  try {
    const incidentsResponse = await fetch('/api/v1/dlq/incidents?limit=100', {
      credentials: 'include'
    });
    if (incidentsResponse.ok) {
      const incidentsData = await incidentsResponse.json();
      updateIncidentSummary(incidentsData);
      updateActiveIncidents(incidentsData);
    } else {
      errorMessages.push('Incidents unavailable');
      hasError = true;
    }
  } catch (error) {
    console.error('Error loading incidents:', error);
    errorMessages.push('Incidents unavailable');
    hasError = true;
  }
  
  // Load classifications
  try {
    const classificationsResponse = await fetch('/api/v1/dlq/classifications', {
      credentials: 'include'
    });
    if (classificationsResponse.ok) {
      const classificationsData = await classificationsResponse.json();
      updateFailureBreakdown(classificationsData);
    } else {
      errorMessages.push('Classifications unavailable');
      hasError = true;
    }
  } catch (error) {
    console.error('Error loading classifications:', error);
    errorMessages.push('Classifications unavailable');
    hasError = true;
  }
  
  // Load trends
  try {
    const trendsResponse = await fetch('/api/v1/dlq/trends', {
      credentials: 'include'
    });
    if (trendsResponse.ok) {
      const trendsData = await trendsResponse.json();
      updateTrendGraph(trendsData);
    } else {
      errorMessages.push('Trends unavailable');
      hasError = true;
    }
  } catch (error) {
    console.error('Error loading trends:', error);
    errorMessages.push('Trends unavailable');
    hasError = true;
  }
  
  // Load root causes
  try {
    const rootCausesResponse = await fetch('/api/v1/dlq/root-causes', {
      credentials: 'include'
    });
    if (rootCausesResponse.ok) {
      const rootCausesData = await rootCausesResponse.json();
      updateRootCauses(rootCausesData);
    } else {
      errorMessages.push('Root causes unavailable');
      hasError = true;
    }
  } catch (error) {
    console.error('Error loading root causes:', error);
    errorMessages.push('Root causes unavailable');
    hasError = true;
  }
  
  // Load top destinations
  await loadTopDestinations();
  
  // Always show content even if some data failed to load
  loadingEl.style.display = 'none';
  contentEl.style.display = 'block';
  
  // Show warning if there were errors, but don't block the tab
  if (hasError) {
    errorEl.textContent = 'Warning: Some data unavailable (' + errorMessages.join(', ') + '). The tab will remain open.';
    errorEl.style.display = 'block';
    errorEl.style.background = '#fdcb6e';
    errorEl.style.color = '#2d3436';
  }
  
  console.log('DLQ Intelligence loaded successfully');
}

function updateHealthScore(healthData) {
  const score  = healthData.overall_score;
  const status = healthData.health_status;

  const rowEl   = document.getElementById('health-score-circle');
  const valueEl = document.getElementById('health-score-value');
  const statusEl = document.getElementById('health-status');
  const descEl   = document.getElementById('health-description');
  const barEl    = document.getElementById('dlq-score-bar');

  if (!valueEl || !statusEl || !descEl) return;

  valueEl.textContent  = score;
  statusEl.textContent = status.charAt(0) + status.slice(1).toLowerCase();

  // Drive the score bar: height = score%, color = signal
  if (barEl) {
    barEl.style.height = score + '%';
    const barColor = score >= 90 ? 'var(--success)' : score >= 70 ? 'var(--warn)' : 'var(--danger)';
    barEl.style.background = barColor;
  }

  // Apply border-left signal to the row
  if (rowEl) {
    rowEl.style.borderLeftColor =
      score >= 90 ? 'var(--success)' :
      score >= 70 ? 'var(--warn)'    : 'var(--danger)';
    rowEl.style.borderLeftWidth = '3px';
  }

  const descriptions = {
    HEALTHY:   'Overall system health is good',
    WARNING:   'System health is degraded, monitor closely',
    DEGRADED:  'System performance is significantly impacted',
    UNHEALTHY: 'System is experiencing major issues',
    CRITICAL:  'System is in critical state, immediate action required',
  };
  descEl.textContent = descriptions[status] || 'Unknown status';
}

function updateIncidentSummary(incidentsData) {
  const incidents = incidentsData.incidents || [];
  
  const totalEl = document.getElementById('total-incidents');
  const openEl = document.getElementById('open-incidents');
  const criticalEl = document.getElementById('critical-incidents');
  const affectedEl = document.getElementById('affected-destinations');
  
  totalEl.textContent = incidents.length;
  openEl.textContent = incidents.filter(i => i.state === 'OPEN').length;
  criticalEl.textContent = incidents.filter(i => i.severity === 'critical' && i.state === 'OPEN').length;
  
  const uniqueDestinations = new Set(incidents.map(i => i.destination_id).filter(Boolean));
  affectedEl.textContent = uniqueDestinations.size;
}

function updateFailureBreakdown(classificationsData) {
  const container = document.getElementById('failure-breakdown');
  const classifications = classificationsData.classifications || [];
  
  if (classifications.length === 0) {
    container.innerHTML = '<p style="color: #b2bec3; text-align: center;">No failures recorded</p>';
    return;
  }
  
  const total = classifications.reduce((sum, c) => sum + c.count, 0);
  
  container.innerHTML = classifications.map(c => {
    const percentage = (c.count / total * 100).toFixed(1);
    const colors = {
      'AUTHENTICATION': '#d63031',
      'AUTHORIZATION': '#e17055',
      'RATE_LIMITING': '#fdcb6e',
      'CLIENT_ERROR': '#00b894',
      'SERVER_ERROR': '#0984e3',
      'NETWORK': '#6c5ce7',
      'TIMEOUT': '#a29bfe',
      'DNS': '#fd79a8',
      'SSL': '#e84393',
      'TRANSFORM': '#00cec9',
      'FILTER': '#81ecec',
      'CIRCUIT_BREAKER': '#b2bec3',
      'CONFIGURATION': '#636e72',
      'UNKNOWN': '#2d3436'
    };
    
    const color = colors[c.category] || '#74b9ff';
    
    return `
      <div class="failure-category">
        <div class="failure-category-info">
          <span>${c.category}</span>
          <span>${c.count} (${percentage}%)</span>
        </div>
        <div class="failure-category-bar">
          <div class="failure-category-fill" style="width: ${percentage}%; background: ${color};"></div>
        </div>
      </div>
    `;
  }).join('');
}

function updateTrendGraph(trendsData) {
  const container = document.getElementById('trend-graph');
  const trends = trendsData.trends || {};
  const trendState = trendsData.trend_state || 'STABLE';
  
  const trendStateEl = document.getElementById('trend-state');
  trendStateEl.textContent = trendState;
  
  const trendColors = {
    'STABLE': '#00b894',
    'SLOW_GROWTH': '#fdcb6e',
    'MODERATE_GROWTH': '#fd79a8',
    'RAPID_GROWTH': '#e17055',
    'EXPLOSIVE_GROWTH': '#d63031'
  };
  trendStateEl.style.color = trendColors[trendState] || '#74b9ff';
  
  const windows = ['15m', '1h', '6h', '24h'];
  const values = windows.map(w => trends[w] || 0);
  const maxValue = Math.max(...values, 1);
  
  container.innerHTML = windows.map((window, index) => {
    const value = values[index];
    const height = (value / maxValue * 100);
    const color = trendColors[trendState] || '#74b9ff';
    
    return `
      <div class="trend-bar" style="height: ${height}%; background: linear-gradient(to top, #0f3460, ${color});">
        <div class="trend-bar-label">${window}</div>
      </div>
    `;
  }).join('');
}

function updateRootCauses(rootCausesData) {
  const container = document.getElementById('root-causes');
  const rootCauses = rootCausesData.root_causes || [];
  
  if (rootCauses.length === 0) {
    container.innerHTML = '<p style="color: #b2bec3; text-align: center;">No root causes identified</p>';
    return;
  }
  
  container.innerHTML = rootCauses.slice(0, 5).map(rc => `
    <div class="root-cause-item">
      <div class="root-cause-header">
        <span class="root-cause-category">${rc.category}</span>
        <span class="root-cause-count">${rc.count}</span>
      </div>
      <div class="root-cause-subcategory">${rc.subcategory || 'General'}</div>
    </div>
  `).join('');
}

function updateActiveIncidents(incidentsData) {
  const container = document.getElementById('active-incidents');
  const incidents = incidentsData.incidents || [];
  
  const openIncidents = incidents.filter(i => i.state === 'OPEN' || i.state === 'INVESTIGATING');
  
  if (openIncidents.length === 0) {
    container.innerHTML = '<p style="color: #b2bec3; text-align: center;">No active incidents</p>';
    return;
  }
  
  container.innerHTML = openIncidents.slice(0, 10).map(incident => {
    const isCritical = incident.severity === 'critical';
    const timeAgo = getTimeAgo(incident.last_seen_at);
    
    return `
      <div class="incident-item ${isCritical ? 'critical' : 'warning'}">
        <div class="incident-header">
          <span class="incident-title">${incident.root_cause || incident.failure_category || 'Unknown'}</span>
          <span class="incident-state ${incident.state}">${incident.state}</span>
        </div>
        <div class="incident-details">
          <div>Affected: ${incident.affected_webhook_count} webhooks</div>
          <div>Last seen: ${timeAgo}</div>
          <div>Severity: ${incident.severity || 'unknown'}</div>
          <div>Trend: ${incident.trend_state || 'unknown'}</div>
        </div>
        ${incident.recommended_action ? `
          <div class="incident-recommendation">
            <strong>Recommendation:</strong> ${incident.recommended_action}
          </div>
        ` : ''}
      </div>
    `;
  }).join('');
}

async function loadTopDestinations() {
  try {
    const response = await fetch('/api/v1/destinations', {
      credentials: 'include'
    });
    if (!response.ok) return;
    
    const destinationsData = await response.json();
    const destinations = destinationsData.destinations || [];
    
    const container = document.getElementById('top-destinations');
    const healthPromises = destinations.slice(0, 5).map(async (dest) => {
      try {
        const healthResponse = await fetch(`/api/v1/destinations/${dest.id}/health`, {
          credentials: 'include'
        });
        if (!healthResponse.ok) return null;
        return await healthResponse.json();
      } catch (error) {
        console.error('Error loading destination health:', error);
        return null;
      }
    });
    
    const healthResults = await Promise.all(healthPromises);
    
    const destinationsWithHealth = destinations
      .slice(0, 5)
      .map((dest, index) => ({
        ...dest,
        health: healthResults[index]
      }))
      .filter(d => d.health && d.health.metrics.failure_rate > 0)
      .sort((a, b) => b.health.metrics.dlq_count - a.health.metrics.dlq_count);
    
    if (destinationsWithHealth.length === 0) {
      container.innerHTML = '<p style="color: #b2bec3; text-align: center;">No failing destinations</p>';
      return;
    }
    
    container.innerHTML = destinationsWithHealth.map(dest => {
      const health = dest.health;
      const healthStatus = health.health_status || 'HEALTHY';
      
      return `
        <div class="destination-item">
          <div class="destination-info">
            <span class="destination-health-badge ${healthStatus.toLowerCase()}">${healthStatus}</span>
            <div>
              <div style="font-weight: bold; color: #dfe6e9;">${dest.name}</div>
              <div style="font-size: 11px; color: #b2bec3;">${dest.url.substring(0, 30)}...</div>
            </div>
          </div>
          <div class="destination-metrics">
            <div>DLQ: ${health.metrics.dlq_count}</div>
            <div>Failure Rate: ${health.metrics.failure_rate.toFixed(1)}%</div>
          </div>
        </div>
      `;
    }).join('');
    
  } catch (error) {
    console.error('Error loading top destinations:', error);
  }
}

function getTimeAgo(dateString) {
  if (!dateString) return 'Unknown';
  
  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now - date;
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);
  
  if (diffMins < 1) return 'Just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString();
}
