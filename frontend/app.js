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
    loadStats(),
    loadWebhooks(1),
    loadDestinations(),
  ]);
  document.getElementById('settings-api-key').value = currentProject?.api_key || '';
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
        loadStats();
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
  if (page === 'overview') { loadStats(); loadOverviewTable(); }
  if (page === 'webhooks') loadWebhooks(1);
  if (page === 'destinations') loadDestinations();
  if (page === 'alerts') loadAlerts();
  if (page === 'team') loadTeam();
  if (page === 'event-types') loadEventTypes();
  if (page === 'simulate') initSimulator();
  if (page === 'replay') initReplay();
  if (page === 'analytics') loadAnalytics();
  if (page === 'ai') { /* static UI, no load needed */ }
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
    document.getElementById('stat-total').textContent = s.total_webhooks;
    document.getElementById('stat-pending').textContent = s.pending_count;
    document.getElementById('stat-processing').textContent = s.processing_count;
    document.getElementById('stat-completed').textContent = s.completed_count;
    document.getElementById('stat-failed').textContent = s.failed_count;
    document.getElementById('stat-rate').textContent = s.success_rate + '%';

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
    renderDestGrid();
    populateDestSelects();
  } catch {}
}

function renderDestGrid() {
  const grid = document.getElementById('dest-grid');
  if (!grid) return;
  if (!destinations.length) {
    grid.innerHTML = `<div class="empty-state"><div class="icon">🎯</div><div class="title">No destinations</div><div class="desc">Create one to start routing</div></div>`;
    return;
  }
  grid.innerHTML = destinations.map(d => {
    const circuitColor = d.circuit_state === 'closed' ? 'completed' : d.circuit_state === 'half_open' ? 'pending' : 'failed';
    return `
    <div class="dest-card">
      <div class="dest-card-header">
        <div style="min-width:0">
          <div class="dest-name">${esc(d.name)}</div>
          <div class="dest-url">${esc(d.url)}</div>
        </div>
        <div style="display:flex;flex-direction:column;align-items:flex-end;gap:4px;flex-shrink:0">
          <span class="badge ${d.is_enabled ? 'completed' : 'failed'}">${d.is_enabled ? 'enabled' : 'disabled'}</span>
        </div>
      </div>
      <div class="dest-meta">
        <span class="dest-tag circuit-${d.circuit_state}">⬤ ${d.circuit_state}</span>
        <span class="dest-tag">Retries: ${d.max_retries}</span>
        ${d.filter_expression ? `<span class="dest-tag">Filtered</span>` : ''}
        ${d.transform_type && d.transform_type !== 'none' ? `<span class="dest-tag">Transform: ${d.transform_type}</span>` : ''}
        ${d.webhook_secret ? `<span class="dest-tag">Signed</span>` : ''}
      </div>
      <div class="dest-actions">
        <button class="btn btn-xs btn-secondary" onclick="viewDestSla('${d.id}', '${esc(d.name)}')">SLA</button>
        <button class="btn btn-xs btn-secondary" onclick="testDestination('${d.id}')">Test</button>
        <button class="btn btn-xs btn-danger" onclick="deleteDestination('${d.id}')">Delete</button>
      </div>
    </div>
  `;
  }).join('');
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
