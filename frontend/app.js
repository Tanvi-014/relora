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
let _showTestEvents = false;
let destinations = [];
let ws = null;
let wsReconnectTimer = null;
let _wsEverConnected = false;
let searchTimer = null;
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
const feedEvents = [];
const FEED_MAX = 50;
let _pendingQueue = 0;
let _editingDestId = null;
let _setupGuideDismissed = false;

// Mission control state — shared between loadDashboard and loadSystemStatus
let _mcWorkerOk = true;
let _mcPending = 0;
let _mcKpis = null;
let _mcSystemStatus = 'healthy';

// DLQ global state — updated by background poll
let _dlqDepth = 0;
let _dlqPollTimer = null;
const _rfIcons = {
  ok:   `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`,
  err:  `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`,
  warn: `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
  info: `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`,
};

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
    // Welcome toast for new signups
    if (new URLSearchParams(window.location.search).get('welcome') === '1') {
      history.replaceState({}, '', '/app.html');
      setTimeout(() => toast('Welcome to Relora! Follow the setup steps above to go live.', 'success'), 800);
    }
    // Start event-driven onboarding checklist
    _startOnboardingPolling();
    // Tick the banner so "last delivery Xs ago" stays accurate
    setInterval(_renderMCBanner, 15000);
    // Background DLQ poll — runs on every page, keeps indicators current
    await _pollDLQState();
    _dlqPollTimer = setInterval(_pollDLQState, 30000);
  } catch {
    // Not logged in → redirect
    window.location.href = '/login.html';
  }
}

// ---------------------------------------------------------------------------
// DLQ background poll + indicators
// ---------------------------------------------------------------------------
async function _pollDLQState() {
  try {
    const data = await apiFetch('/dlq/health');
    const depth = data?.components?.dlq_size?.value ?? 0;
    const prevDepth = _dlqDepth;
    _dlqDepth = depth;
    _updateDLQIndicators(depth);
    // Refresh notifications if DLQ state changed
    if (depth !== prevDepth) loadNotifications();
    // DLQ just cleared — auto-resolve any orphaned open incidents
    if (depth === 0 && prevDepth > 0) {
      apiFetch('/dlq/resolve-all-incidents', { method: 'POST' }).catch(() => {});
    }
  } catch {}
}

function _updateDLQIndicators(depth) {
  document.title = 'Relora';

  // Nav badge on the DLQ nav item
  const badge = document.getElementById('dlq-nav-badge');
  const navItem = document.querySelector('[data-page="dlq-intelligence"]');
  if (badge) {
    badge.textContent = depth > 9 ? '9+' : depth;
    badge.style.display = depth > 0 ? '' : 'none';
  }
  if (navItem) navItem.classList.toggle('nav-item--dlq-alert', depth > 0);

  // "Clear all" button — only shown when there are failed events
  const clearBtn = document.getElementById('dlq-clear-btn');
  if (clearBtn) clearBtn.style.display = depth > 0 ? '' : 'none';

  // Page-pulse DLQ counter color
  const ppDlq = document.getElementById('pp-dlq');
  if (ppDlq) {
    ppDlq.textContent = depth;
    ppDlq.className = depth === 0 ? '' : depth < 10 ? 'pp-warn' : 'pp-crit';
  }
}

// ---------------------------------------------------------------------------
// API fetch helper (uses httpOnly cookie automatically)
// ---------------------------------------------------------------------------
async function apiFetch(path, opts = {}) {
  const projectHeader = currentProject?.id ? { 'X-Project-Id': currentProject.id } : {};
  const res = await fetch(API + path, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...projectHeader, ...(opts.headers || {}) },
    ...opts,
  });
  if (res.status === 401) { window.location.href = '/login.html'; throw new Error('401'); }
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const j = await res.json();
      if (typeof j.detail === 'string') detail = j.detail;
      else if (Array.isArray(j.detail)) detail = j.detail.map(d => d.msg || JSON.stringify(d)).join('; ');
      else if (j.detail != null) detail = JSON.stringify(j.detail);
      else if (typeof j.message === 'string') detail = j.message;
    } catch {}
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
    document.getElementById('notif-panel')?.classList.remove('open');
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
      openModal('modal-new-project');
      return;
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
  // Note: click handler is set once via onclick="togglePSDropdown(event)" in HTML — no addEventListener here.
}

function togglePSDropdown(e) {
  e.stopPropagation();
  // If the click originated inside the project list (i.e. a project item), let switchProject handle it.
  if (document.getElementById('ps-list').contains(e.target)) return;
  document.getElementById('ps-dropdown').classList.toggle('open');
  document.getElementById('user-dropdown').classList.remove('open');
  document.getElementById('notif-panel')?.classList.remove('open');
  document.getElementById('notif-btn')?.classList.remove('has-unread-open');
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
  loadNotifications();
  document.getElementById('settings-api-key').value = currentProject?.api_key || '';
  const ingestUrl = `${window.location.origin}/api/v1/ingest`;
  const ingestEl = document.getElementById('settings-ingest-url');
  if (ingestEl) ingestEl.value = ingestUrl;
  const destIngestEl = document.getElementById('dest-ingest-url');
  if (destIngestEl) destIngestEl.value = ingestUrl;
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
    if (_wsEverConnected) toast('Live updates reconnected', 'success');
    _wsEverConnected = true;
    if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }
  };

  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === 'webhook.updated') {
        const d = msg.data;
        updateWebhookRowLive(d);

        // If the detail panel is showing this webhook and the webhooks page is active,
        // refresh the panel — debounced so rapid retries don't create a request storm.
        const activePage = document.querySelector('.page.active')?.id;
        if (selectedWebhookId === d.id && activePage === 'page-webhooks') _debouncedOpenPanel(d.id);

        // Push to activity feed + pulse arch viz (skip sandbox/demo deliveries)
        const destLabel = _shortUrl(d.destination_url);
        if (!d.is_sandbox) {
          if (d.status === 'completed') {
            _pushFeedEvent('ok', 'Delivery succeeded', destLabel, d.updated_at);
            _arcVizPulse('ok');
            _pipelineNodeFlash('pipe-node-relay', 'ok');
            _triggerFirstDeliveryCelebration(d);
          } else if (d.status === 'failed') {
            _pushFeedEvent('err', 'Delivery failed', destLabel, d.updated_at);
            _arcVizPulse('err');
            _pipelineNodeFlash('pipe-node-dlq', 'err');
          }
        }

        // Always refresh the DLQ badge count so indicators stay accurate
        _pollDLQState();

        // Refresh whichever page is currently active
        const wsPageRefresh = {
          'page-overview': loadDashboard,
          'page-webhooks': loadStats,
        };
        if (activePage === 'page-dlq-intelligence' && d.status === 'completed') {
          _debouncedDLQReload();
        } else if (wsPageRefresh[activePage]) {
          wsPageRefresh[activePage]();
        }
      }
    } catch {}
  };

  ws.onclose = () => {
    setWsDot('disconnected');
    if (_wsEverConnected) toast('Live updates disconnected — reconnecting…', 'info');
    wsReconnectTimer = setTimeout(connectWS, 3000);
  };

  ws.onerror = () => ws.close();
}

function reconnectWS() {
  if (ws) ws.close();
  setTimeout(connectWS, 100);
}

const _debouncedDLQReload = debounce(loadDLQIntelligence, 800);
const _debouncedOpenPanel = debounce(openPanel, 600);

function setWsDot(state) {
  const dot = document.getElementById('ws-dot');
  const txt = document.getElementById('ws-status-text');
  dot.className = 'ws-dot ' + state;
  txt.textContent = state === 'connected' ? 'Live' : state === 'connecting' ? 'Connecting…' : 'Disconnected';
  const feedLive = document.getElementById('rel-feed-live');
  if (feedLive) feedLive.className = 'rel-feed-live' + (state === 'connected' ? ' rel-feed-live--on' : '');
  const livePill = document.getElementById('mc-live-pill');
  if (livePill) livePill.className = 'mc-live-pill' + (state === 'connected' ? ' live-on' : '');
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
// Pipeline — live flash + auto-refresh
// ---------------------------------------------------------------------------
let _pipelineRefreshTimer = null;

function _pipelineNodeFlash(nodeId, type) {
  const node = document.getElementById(nodeId);
  if (!node) return;
  const cls = 'pipe-node--flash-' + type;
  node.classList.remove('pipe-node--flash-ok', 'pipe-node--flash-err');
  void node.offsetWidth; // force reflow so animation restarts
  node.classList.add(cls);
  setTimeout(() => node.classList.remove(cls), 900);
}

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------
var _auditTab = 'log';

function switchAuditTab(tab) {
  _auditTab = tab;
  document.querySelectorAll('[data-audittab]').forEach(t => t.classList.toggle('active', t.dataset.audittab === tab));
  const logPanel = document.getElementById('audit-tab-log');
  const tlPanel  = document.getElementById('audit-tab-timeline');
  if (logPanel) logPanel.style.display = tab === 'log' ? '' : 'none';
  if (tlPanel)  tlPanel.style.display  = tab === 'timeline' ? '' : 'none';
  if (tab === 'log')      loadAuditLog();
  if (tab === 'timeline') loadTimeline();
}

function refreshAuditTab() {
  if (_auditTab === 'log') loadAuditLog();
  else loadTimeline();
}

function navTo(page) {
  // Merged-page redirects — removed sidebar items route to their parent page + tab
  if (page === 'alerts')    { navTo('recovery'); switchRecoveryTab('alert-settings'); return; }
  if (page === 'analytics') { navTo('insights'); switchInsightTab('trends'); return; }

  // Timeline is inside Audit
  if (page === 'timeline') { navTo('audit'); switchAuditTab('timeline'); return; }

  // Stop pipeline auto-refresh when leaving that page
  if (page !== 'pipeline' && _pipelineRefreshTimer) {
    clearInterval(_pipelineRefreshTimer);
    _pipelineRefreshTimer = null;
  }

  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  const pageEl = document.getElementById('page-' + page);

  // Sub-pages not in sidebar: highlight their parent nav item instead
  const _navParent = { 'dlq-intelligence': 'recovery', 'replay': 'recovery', 'pipeline': 'webhooks' };
  const navPage = _navParent[page] || page;
  const navEl = document.querySelector(`.nav-item[data-page="${navPage}"]`);

  if (pageEl) pageEl.classList.add('active');
  if (navEl) navEl.classList.add('active');

  // Show the system heartbeat strip on all pages except overview (which has the full KPI strip)
  const pulse = document.getElementById('page-pulse');
  if (pulse) {
    if (page === 'overview') {
      pulse.classList.remove('pp-visible');
    } else {
      pulse.classList.add('pp-visible');
      _updatePagePulse();
    }
  }

  if (localStorage.getItem('relora_onboarding_dismissed') !== '1' && _obsStep() !== 'done') {
    _renderSuggestionBar();
  }

  // Lazy-load page data
  if (page === 'overview') { _setupGuideDismissed = false; loadDashboard(); }
  if (page === 'webhooks') loadWebhooks(1);
  if (page === 'destinations') loadDestinations();
  if (page === 'team') loadTeam();
  if (page === 'replay') initReplay();
  if (page === 'dlq-intelligence') { loadDLQIntelligence(); }
  if (page === 'schema-drift') { loadSchemaDrift(); }
  if (page === 'audit') { switchAuditTab(_auditTab); }
  if (page === 'settings') { loadSigningSecrets(); }
  if (page === 'recovery') { loadRecovery(); }
  if (page === 'insights') { loadInsights(); }
  if (page === 'pipeline') {
    loadPipeline();
    if (!_pipelineRefreshTimer) {
      _pipelineRefreshTimer = setInterval(loadPipeline, 30000);
    }
  }
}

// Close dropdowns on outside click
document.addEventListener('click', () => {
  document.getElementById('ps-dropdown').classList.remove('open');
  document.getElementById('user-dropdown').classList.remove('open');
  document.getElementById('notif-panel')?.classList.remove('open');
  document.getElementById('notif-btn')?.classList.remove('has-unread-open');
});

// ⌘K / Ctrl+K focuses the header search
document.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
    e.preventDefault();
    const inp = document.getElementById('header-search-input');
    if (inp) { inp.focus(); inp.select(); }
  }
});

function _headerSearch(query) {
  if (!query.trim()) return;
  navTo('webhooks');
  setTimeout(() => {
    const inp = document.getElementById('wh-search');
    if (inp) {
      inp.value = query.trim();
      loadWebhooks(1);
    }
    const headerInp = document.getElementById('header-search-input');
    if (headerInp) headerInp.value = '';
  }, 50);
}

// ---------------------------------------------------------------------------
// Notification center
// ---------------------------------------------------------------------------
const _NOTIF_READ_KEY = 'relora_notifs_read';

function _getReadIds() {
  try { return new Set(JSON.parse(localStorage.getItem(_NOTIF_READ_KEY) || '[]')); } catch { return new Set(); }
}
function _markRead(id) {
  const ids = _getReadIds();
  ids.add(id);
  localStorage.setItem(_NOTIF_READ_KEY, JSON.stringify([...ids]));
}
function _markAllRead(ids) {
  localStorage.setItem(_NOTIF_READ_KEY, JSON.stringify(ids));
}

let _notifItems = [];

async function loadNotifications() {
  const items = [];

  // ── Onboarding suggestions ────────────────────────────────────────────────
  if (destinations.length === 0) {
    items.push({
      id: 'onboarding-dest',
      type: 'onboarding',
      title: 'Add your first destination',
      desc: 'Configure where Relora should deliver your webhooks.',
      action: () => openDestModal(),
    });
  }
  if (destinations.length > 0 && destinations.length < 2) {
    items.push({
      id: 'onboarding-alerts',
      type: 'onboarding',
      title: 'Get notified when deliveries fail',
      desc: 'Connect Slack or email so you hear about failures before your customers do.',
      action: () => { navTo('alerts'); requestAnimationFrame(openAlertModal); },
    });
  }

  // ── Weekly Insights report ────────────────────────────────────────────────
  try {
    const report = await apiFetch('/insights/reports/current');
    if (report && report.week_start) {
      const ws   = new Date(report.week_start);
      const we   = new Date(report.week_end);
      const wEnd = new Date(we.getTime() - 86400000);
      const lbl  = ws.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ' – ' +
        wEnd.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
      items.push({
        id: 'insight-' + report.week_start,
        type: 'insight',
        title: 'Weekly Reliability Briefing ready',
        desc: `${lbl} · Grade ${report.grade} · ${(report.reliability_score ?? 100).toFixed(1)}% reliability`,
        action: () => navTo('insights'),
      });
    }
  } catch {}

  // ── DLQ alert — surfaced whenever the queue is non-empty ─────────────────
  if (_dlqDepth > 0) {
    items.push({
      id: 'dlq-nonempty',
      type: 'dlq',
      title: `${_dlqDepth} event${_dlqDepth !== 1 ? 's' : ''} could not be delivered`,
      desc: 'These events failed all retry attempts and need your attention. Click to review and replay.',
      action: () => navTo('dlq-intelligence'),
    });
  }

  // ── Active incidents ──────────────────────────────────────────────────────
  if (_mcKpis && _mcKpis.open_incidents > 0) {
    const n = _mcKpis.open_incidents;
    items.push({
      id: 'incidents-open-' + n,
      type: 'incident',
      title: `${n} open incident${n > 1 ? 's' : ''} require attention`,
      desc: 'Review active incidents and take action before they escalate.',
      action: () => navTo('dlq-intelligence'),
    });
  }

  _notifItems = items;
  _renderNotifBadge();
  _renderNotifList();
}

function _renderNotifBadge() {
  const readIds  = _getReadIds();
  const unread   = _notifItems.filter(n => !readIds.has(n.id));
  const badge    = document.getElementById('notif-badge');
  const btn      = document.getElementById('notif-btn');
  if (!badge || !btn) return;
  const wasUnread = btn.classList.contains('has-unread');
  if (unread.length > 0) {
    badge.textContent = unread.length > 9 ? '9+' : unread.length;
    badge.style.display = '';
    if (!wasUnread) {
      // Re-trigger ring animation by toggling has-unread
      btn.classList.remove('has-unread');
      void btn.offsetWidth;
    }
    btn.classList.add('has-unread');
  } else {
    badge.style.display = 'none';
    btn.classList.remove('has-unread');
  }
}

function _renderNotifList() {
  const el = document.getElementById('notif-list');
  if (!el) return;
  const readIds = _getReadIds();
  if (!_notifItems.length) {
    el.innerHTML = '<div class="notif-empty">All caught up — no notifications.</div>';
    return;
  }
  const iconSvg = {
    insight: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>`,
    incident: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
    dlq: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/><path d="M11 8v3l2 2"/></svg>`,
    onboarding: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12h14"/></svg>`,
    product: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="2,12 7,12 9,5 11,19 13,12 22,12"/></svg>`,
  };
  el.innerHTML = _notifItems.map(item => {
    const unread = !readIds.has(item.id);
    return `<div class="notif-item${unread ? ' unread' : ''}" onclick="_notifClick('${item.id}')">
      ${unread ? '<div class="notif-unread-dot"></div>' : ''}
      <div class="notif-icon notif-icon-${item.type}">${iconSvg[item.type] || ''}</div>
      <div class="notif-body">
        <div class="notif-title">${esc(item.title)}</div>
        <div class="notif-desc">${esc(item.desc)}</div>
      </div>
    </div>`;
  }).join('');
}

function _notifClick(id) {
  _markRead(id);
  _renderNotifBadge();
  _renderNotifList();
  const item = _notifItems.find(n => n.id === id);
  if (item?.action) {
    document.getElementById('notif-panel')?.classList.remove('open');
    item.action();
  }
}

function markAllNotifsRead() {
  _markAllRead(_notifItems.map(n => n.id));
  _renderNotifBadge();
  _renderNotifList();
}

function toggleNotifPanel(e) {
  e.stopPropagation();
  const panel = document.getElementById('notif-panel');
  const isOpen = panel.classList.contains('open');
  document.getElementById('user-dropdown').classList.remove('open');
  document.getElementById('ps-dropdown').classList.remove('open');
  panel.classList.toggle('open', !isOpen);
}

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
    const [d] = await Promise.all([
      apiFetch('/dashboard'),
      loadSystemStatus(),
    ]);

    // Store for cross-function access
    _mcKpis = { ...d.kpis, deliveries_today: d.deliveries_today ?? 0, last_delivery_at: d.last_delivery_at ?? null };
    _mcSystemStatus = d.system_status;

    // Mission control renderers
    _renderKPIStrip(d.kpis, d.active_incidents || [], d.recent_failures || []);
    _renderMCBanner();
    _renderMCTriage(d.kpis, d.active_incidents || [], d.slo_breaches || [], d.unacked_schema_changes || 0);
    _renderMCDLQ(d.kpis?.dlq_depth ?? 0);
    _renderMCDestinations();
    _syncFeedFromDashboard(d.recent_failures || [], d.active_incidents || [], d.recent_activity || []);

    // Legacy renderers — write to hidden ghost IDs, safe to keep
    _renderHealthBanner(d.system_status);
    _renderKPIs(d.kpis);
    _renderHealthSplit(d.health_split || {});
    _renderSloBreaches(d.slo_breaches || []);
    _renderSchemaDriftAlert(d.unacked_schema_changes || 0);
    _renderIncidents(d.active_incidents || []);
    _renderSparkline(d.sparkline || []);
    _renderRecentFailures(d.recent_failures || []);
    _renderCommandCenter(d.kpis, d.active_incidents || []);
    _renderWhatChanged(d.kpis, d.active_incidents || []);
    _renderArchViz(d.kpis);
    _updatePagePulse();
    document.getElementById('mission-control')?.querySelector('.dashboard-error')?.remove();
    _renderOnboardingSection();
    _renderSuggestionBar();
  } catch (e) {
    const mc = document.getElementById('mission-control');
    if (mc) {
      const existing = mc.querySelector('.dashboard-error');
      const msg = 'Dashboard data unavailable — ' + (e.message || 'check your connection');
      if (existing) {
        existing.textContent = msg;
      } else {
        const err = document.createElement('div');
        err.className = 'dashboard-error';
        err.style.cssText = 'padding:16px;color:var(--danger);font-size:13px;';
        err.textContent = msg;
        mc.prepend(err);
      }
    }
  }
}

function _updatePagePulse() {
  const kpis = _mcKpis || {};
  const rate = kpis.success_rate_24h;
  const dlq = kpis.dlq_depth ?? 0;
  const incidents = kpis.open_incidents ?? 0;
  const delivered = kpis.deliveries_today ?? 0;

  const rateEl = document.getElementById('pp-rate');
  const dlqEl  = document.getElementById('pp-dlq');
  const incEl  = document.getElementById('pp-incidents');
  const delEl  = document.getElementById('pp-delivered');
  const dotEl  = document.getElementById('pp-dot');

  if (rateEl) {
    rateEl.textContent = rate != null ? rate.toFixed(1) + '%' : '—';
    rateEl.className = rate == null ? '' : rate >= 99 ? 'pp-ok' : rate >= 95 ? 'pp-warn' : 'pp-crit';
  }
  if (dlqEl) {
    dlqEl.textContent = dlq;
    dlqEl.className = dlq === 0 ? '' : dlq < 10 ? 'pp-warn' : 'pp-crit';
  }
  if (incEl) {
    incEl.textContent = incidents;
    incEl.className = incidents === 0 ? '' : 'pp-warn';
  }
  if (delEl) {
    delEl.textContent = delivered > 0 ? delivered.toLocaleString() : '0';
  }
  if (dotEl) {
    const isCrit = (rate != null && rate < 90) || incidents > 0;
    const isWarn = dlq > 0 || (rate != null && rate < 99);
    dotEl.className = 'pp-dot' + (isCrit ? ' pp-crit' : isWarn ? ' pp-warn' : '');
  }
}

async function loadSystemStatus() {
  const row = document.getElementById('sys-status-row');
  try {
    const hRes = await fetch('/health/detailed', { credentials: 'include' });
    const h = hRes.ok ? await hRes.json() : {};
    const worker = h.checks?.worker || {};
    const queue  = h.checks?.queue  || {};
    const pending = queue.depth ?? 0;
    _pendingQueue = pending;
    _mcPending = pending;
    const stuckJobs = worker.stuck_jobs ?? 0;
    const workerOk = worker.status === 'ok';
    _mcWorkerOk = workerOk;

    // Update pending KPI card
    const pendingEl  = document.getElementById('kpi-pending');
    const pendingCard = document.getElementById('kpi-pending-card');
    if (pendingEl) {
      pendingEl.textContent = pending.toLocaleString();
      pendingEl.className = 'kpi-value ' + (pending === 0 ? 'kpi-good' : pending < 50 ? '' : 'kpi-warn');
    }
    if (pendingCard) pendingCard.classList.toggle('kpi-card--warn', pending > 50);

    if (row) row.innerHTML = [
      _sysPill(workerOk ? 'ok' : 'crit',
               workerOk ? 'Worker online' : `Worker stalled — ${stuckJobs} stuck job${stuckJobs !== 1 ? 's' : ''}`),
      _sysPill(pending === 0 ? 'ok' : pending < 50 ? 'warn' : 'crit',
               `${pending.toLocaleString()} pending`),
    ].join('');

    _renderMCBanner();
  } catch {
    if (row) row.innerHTML = '';
  }
}

function _sysPill(cls, label) {
  return `<span class="sys-status-pill ${cls}">${esc(label)}</span>`;
}

// ---------------------------------------------------------------------------
// Event-driven onboarding checklist
// All steps are derived from real product state
// ---------------------------------------------------------------------------
let _onboardingProgress = null;
let _onboardingPollTimer = null;

async function _loadOnboardingProgress() {
  try {
    const prev = _onboardingProgress;
    _onboardingProgress = await apiFetch('/onboarding/progress');
    // Auto-start demo when user creates their first destination
    if (
      prev &&
      !prev.steps[0].completed &&
      _onboardingProgress.steps[0].completed &&
      localStorage.getItem('relora_demo_journey_completed_v1') !== '1' &&
      typeof reloraDemoRead === 'function' && !reloraDemoRead().startedAt
    ) {
      fireDemoEvent();
    }
    _renderOnboardingSection();
    // Re-render banner whenever step state changes
    const changed = !prev ||
      prev.activated !== _onboardingProgress.activated ||
      prev.steps.some((s, i) => s.completed !== _onboardingProgress.steps[i]?.completed);
    if (changed) _renderMCBanner();
    if (_onboardingProgress.activated) _stopOnboardingPolling();
  } catch {
    // Silently fail — onboarding not critical
  }
}

function _startOnboardingPolling() {
  // Initial load
  _loadOnboardingProgress();
  // Poll every 2s for real-time updates
  if (_onboardingPollTimer) clearInterval(_onboardingPollTimer);
  _onboardingPollTimer = setInterval(_loadOnboardingProgress, 2000);
}

function _stopOnboardingPolling() {
  if (_onboardingPollTimer) {
    clearInterval(_onboardingPollTimer);
    _onboardingPollTimer = null;
  }
}

function _renderOnboardingSection() {
  const section = document.getElementById('onboarding-section');
  if (section) section.style.display = 'none';
}

function _hideObsCard() {
  const section = document.getElementById('onboarding-section');
  if (section) section.style.display = 'none';
}

function _dismissSetupGuide() {
  _setupGuideDismissed = true;
  const section = document.getElementById('onboarding-section');
  if (section) section.style.display = 'none';
}

async function fireDemoEvent() {
  const btn = document.getElementById('obs-demo-btn');
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="obs-spinner"></span> Sending…'; }
  try {
    await apiFetch('/onboarding/send-demo', { method: 'POST' });
    if (typeof reloraDemoStart === 'function') reloraDemoStart({ force: false });
    toast('Demo event sent! The checklist will update automatically as the demo progresses.', 'success');
    setTimeout(_loadOnboardingProgress, 1000);
  } catch (e) {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="5 3 19 12 5 21 5 3"/></svg> Send demo event';
    }
    toast(e.message || 'Failed to send demo event', 'error');
  }
}

// Legacy: for backward compatibility only
function _renderSuggestionBar() {}
function _dismissSuggBar() {}
function _obsStep() {
  return destinations.some(d => !d.is_sandbox) ? 'done' : 'active';
}


// ---------------------------------------------------------------------------
// Next Recommended Action card
// ---------------------------------------------------------------------------
let _alertCount = null;

async function _loadAlertCount() {
  if (_alertCount !== null) return;
  try {
    const alerts = await apiFetch('/alerts');
    _alertCount = (alerts || []).filter(a => a.enabled).length;
  } catch { _alertCount = 0; }
}

function _nraCopyCmd(cmd) {
  navigator.clipboard.writeText(cmd).then(() => toast('Copied to clipboard', 'success'));
}

function _triggerFirstDeliveryCelebration(event) {
  const dest = destinations.find(d => d.id === event.destination_id);
  if (dest?.is_sandbox) return;
  if (localStorage.getItem('relora_first_celebrated') === '1') return;
  localStorage.setItem('relora_first_celebrated', '1');
  const name = dest?.name || _shortUrl(event.destination_url || '');
  toast(`First delivery confirmed → ${name}`, 'success');
}

function _renderHealthSplit(split) {
  const el = document.getElementById('health-split');
  if (!el) return;
  const src = split.source_health || 'healthy';
  const dst = split.destination_health || 'healthy';
  const providerNote = split.provider_issue_likely
    ? '<span class="tag tag--warn">Provider issue likely</span>' : '';
  el.innerHTML = `
    <div class="health-split-row">
      <span class="health-split-label">Inbound sources</span>
      <span class="health-split-badge health-split-badge--${src}">${src}</span>
      ${providerNote}
    </div>
    <div class="health-split-row">
      <span class="health-split-label">Outbound destinations</span>
      <span class="health-split-badge health-split-badge--${dst}">${dst}</span>
    </div>`;
}

function _renderSloBreaches(breaches) {
  const el = document.getElementById('slo-breaches');
  if (!el) return;
  if (!breaches.length) { el.innerHTML = ''; return; }
  el.innerHTML = `<div class="inline-banner inline-banner--danger">
    <strong>Reliability goal${breaches.length > 1 ? 's' : ''} not met</strong>
    ${breaches.map(b =>
      `<span class="slo-breach-item">${esc(b.destination_name)}: ${b.current_pct.toFixed(1)}% delivered (goal: ${b.target_pct}%)</span>`
    ).join('')}
  </div>`;
}

function _renderSchemaDriftAlert(count) {
  const el = document.getElementById('schema-drift-alert');
  if (!el) return;
  if (!count) { el.innerHTML = ''; return; }
  el.innerHTML = `<div class="inline-banner inline-banner--warn">
    <strong>Payload shape changed</strong> — your source started sending different event data (${count} change${count > 1 ? 's' : ''} detected). No deliveries were affected.
  </div>`;
}

// ── Event journey modal ────────────────────────────────────────────────────

async function showEventJourney(eventId) {
  if (!eventId) return;
  try {
    const data = await apiFetch(`/events/${encodeURIComponent(eventId)}/journey`);
    const modal = document.getElementById('journey-modal');
    const body = document.getElementById('journey-body');
    if (!modal || !body) { alert(JSON.stringify(data, null, 2)); return; }

    body.innerHTML = `
      <div class="journey-header">
        <div class="journey-meta">Event ID: <code>${esc(eventId)}</code></div>
        <div class="journey-meta">Ingested: ${relTime(data.ingest_time)}</div>
        <div class="journey-meta">Destinations: ${data.destination_count}
          &nbsp;·&nbsp; <span class="status-badge status-${data.overall_status.replace('_','-')}">${data.overall_status.replace(/_/g,' ')}</span>
        </div>
      </div>
      ${data.deliveries.map(d => `
        <div class="journey-delivery">
          <div class="journey-dest">
            <strong>${esc(d.destination_name || d.destination_url)}</strong>
            <span class="status-badge status-${d.status}">${d.status}</span>
            ${d.retry_count > 0 ? `<span class="retry-count">${d.retry_count} retries</span>` : ''}
          </div>
          <div class="journey-attempts">
            ${d.attempts.map(a => `
              <div class="journey-attempt">
                <span class="attempt-num">#${a.attempt_number}</span>
                <span class="attempt-code ${a.status_code >= 200 && a.status_code < 300 ? 'ok' : 'err'}">${a.status_code || '—'}</span>
                <span class="attempt-latency">${a.duration_ms ? a.duration_ms + 'ms' : ''}</span>
                <span class="attempt-time">${relTime(a.attempted_at)}</span>
                ${a.error_message ? `<span class="attempt-error">${esc(a.error_message.slice(0,80))}</span>` : ''}
              </div>`).join('')}
          </div>
        </div>`).join('')}`;
    modal.style.display = 'flex';
  } catch (e) {
    alert('Could not load journey: ' + e.message);
  }
}

// ── Schema drift page ──────────────────────────────────────────────────────

async function loadSchemaDrift() {
  const container = document.getElementById('schema-drift-list');
  if (!container) return;
  container.innerHTML = '<div class="loading-state">Loading…</div>';
  try {
    const data = await apiFetch('/schema-changes?unacknowledged_only=false&limit=100');
    if (!data.changes.length) {
      container.innerHTML = '<div class="empty-state"><div class="title">No schema changes detected yet</div><div class="desc">Relora will fingerprint every inbound payload and alert you here when the structure changes.</div></div>';
      return;
    }
    container.innerHTML = `<table class="data-table"><thead><tr>
      <th>Source</th><th>Added keys</th><th>Removed keys</th><th>Detected</th><th>Status</th><th></th>
    </tr></thead><tbody>
      ${data.changes.map(c => `<tr>
        <td><code>${esc(c.source_key)}</code></td>
        <td class="key-list added">${(c.added_keys||[]).map(k=>`<code>${esc(k)}</code>`).join(' ')}</td>
        <td class="key-list removed">${(c.removed_keys||[]).map(k=>`<code>${esc(k)}</code>`).join(' ')}</td>
        <td>${relTime(c.detected_at)}</td>
        <td>${c.acknowledged_at ? '<span class="tag tag--ok">Acknowledged</span>' : '<span class="tag tag--warn">New</span>'}</td>
        <td>${!c.acknowledged_at ? `<button class="btn btn--sm" onclick="acknowledgeSchemaChange('${c.id}')">Acknowledge</button>` : ''}</td>
      </tr>`).join('')}
    </tbody></table>`;
  } catch (e) {
    container.innerHTML = `<div class="error-state">${esc(e.message)}</div>`;
  }
}

async function acknowledgeSchemaChange(id) {
  await apiFetch(`/schema-changes/${id}/acknowledge`, { method: 'POST' });
  loadSchemaDrift();
  loadDashboard();
}

// ---------------------------------------------------------------------------
// Audit Log
// ---------------------------------------------------------------------------
let _auditEntries = [];
let _auditOffset  = 0;
const AUDIT_LIMIT = 50;

async function loadAuditLog(offset = 0) {
  _auditOffset = offset;
  const wrap = document.getElementById('audit-table-wrap');
  const pag  = document.getElementById('audit-pagination');
  if (!wrap) return;

  const typeFilter   = document.getElementById('audit-type-filter')?.value   || '';
  const actionFilter = document.getElementById('audit-action-filter')?.value || '';

  wrap.innerHTML = '<div style="padding:40px;text-align:center;color:var(--text3);font-size:13px">Loading…</div>';

  let qs = `?limit=${AUDIT_LIMIT}&offset=${offset}`;
  if (typeFilter)   qs += `&resource_type=${encodeURIComponent(typeFilter)}`;
  if (actionFilter) qs += `&action=${encodeURIComponent(actionFilter)}`;

  try {
    const data = await apiFetch('/audit-log' + qs);
    _auditEntries = data.entries || [];

    if (!_auditEntries.length) {
      wrap.innerHTML = `
        <div class="empty-state">
          <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.25">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
            <path d="M14 2v6h6"/><path d="M8 13h8"/><path d="M8 17h5"/>
          </svg>
          <div class="title">No audit entries yet</div>
          <div class="desc">Creating destinations, rotating keys, and starting replay jobs will appear here.</div>
        </div>`;
      if (pag) pag.innerHTML = '';
      return;
    }

    const actionCls = { CREATE: 'completed', UPDATE: 'processing', DELETE: 'failed', REPLAY: 'pending' };
    const resourceLabel = { destination: 'Destination', alert_config: 'Alert', webhook: 'Webhook',
      project: 'Project', replay_job: 'Replay', api_key: 'API Key', team_member: 'Team Member' };

    const rows = _auditEntries.map((e, idx) => {
      const d    = new Date(e.created_at);
      const time = d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      const date = d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
      const a    = e.changes?.after;
      const b    = e.changes?.before;
      const detail = a?.name || b?.name
        ? esc(a?.name || b?.name)
        : a?.url
          ? esc(a.url.substring(0, 50))
          : e.resource_id
            ? `<span class="cell-mono" style="font-size:11px">${esc(e.resource_id.substring(0, 20))}…</span>`
            : '—';
      const hasChanges = e.changes && (e.changes.before || e.changes.after);
      return `
        <tr>
          <td style="white-space:nowrap">
            <div style="font-size:12px;font-weight:500">${time}</div>
            <div style="font-size:11px;color:var(--text3)">${date}</div>
          </td>
          <td><span class="badge ${actionCls[e.action] || 'pending'}">${esc(e.action)}</span></td>
          <td style="font-size:12px;color:var(--text2)">${esc(resourceLabel[e.resource_type] || e.resource_type || '—')}</td>
          <td style="font-size:12px">${detail}</td>
          <td style="font-size:11px;font-family:var(--mono);color:var(--text3)">${esc(e.ip_address || '—')}</td>
          <td>${hasChanges ? `<button class="btn btn-xs btn-secondary" data-idx="${idx}" onclick="toggleAuditDiff(this)">Diff</button>` : ''}</td>
        </tr>
        <tr class="audit-diff-row" style="display:none">
          <td colspan="6" class="audit-diff-cell"></td>
        </tr>`;
    }).join('');

    wrap.innerHTML = `
      <table>
        <thead><tr>
          <th style="width:120px">Time</th>
          <th style="width:90px">Action</th>
          <th style="width:130px">Resource</th>
          <th>Details</th>
          <th style="width:120px">IP Address</th>
          <th style="width:56px"></th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>`;

    if (pag) {
      const hasPrev = offset > 0;
      const hasNext = _auditEntries.length === AUDIT_LIMIT;
      pag.innerHTML = hasPrev || hasNext ? `
        <button class="btn btn-secondary btn-sm" onclick="loadAuditLog(${offset - AUDIT_LIMIT})" ${hasPrev ? '' : 'disabled'}>← Prev</button>
        <span style="font-size:12px;color:var(--text3);padding:0 12px">${offset + 1}–${offset + _auditEntries.length}</span>
        <button class="btn btn-secondary btn-sm" onclick="loadAuditLog(${offset + AUDIT_LIMIT})" ${hasNext ? '' : 'disabled'}>Next →</button>` : '';
    }
  } catch (e) {
    wrap.innerHTML = `<div style="padding:24px;text-align:center;color:var(--danger);font-size:13px">Failed to load: ${esc(e.message)}</div>`;
  }
}

function toggleAuditDiff(btn) {
  const idx      = parseInt(btn.dataset.idx, 10);
  const row      = btn.closest('tr');
  const diffRow  = row.nextElementSibling;
  if (!diffRow?.classList.contains('audit-diff-row')) return;

  const isOpen = diffRow.style.display !== 'none';
  if (isOpen) { diffRow.style.display = 'none'; btn.textContent = 'Diff'; return; }

  const changes = _auditEntries[idx]?.changes || {};
  const cell    = diffRow.querySelector('.audit-diff-cell');
  const cols    = [];
  if (changes.before) cols.push(`
    <div>
      <div style="font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--danger);margin-bottom:6px">Before</div>
      <pre class="json-view" style="font-size:11px;margin:0;max-height:200px;overflow:auto">${esc(JSON.stringify(changes.before, null, 2))}</pre>
    </div>`);
  if (changes.after) cols.push(`
    <div>
      <div style="font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--success);margin-bottom:6px">After</div>
      <pre class="json-view" style="font-size:11px;margin:0;max-height:200px;overflow:auto">${esc(JSON.stringify(changes.after, null, 2))}</pre>
    </div>`);
  cell.innerHTML = `<div style="display:grid;grid-template-columns:${cols.length > 1 ? '1fr 1fr' : '1fr'};gap:16px;padding:14px">${cols.join('')}</div>`;
  diffRow.style.display = '';
  btn.textContent = 'Close';
}

function _renderHealthBanner(status) {
  const banner = document.getElementById('health-banner');
  if (!banner) return;
  const title = document.getElementById('health-banner-title');
  const sub = document.getElementById('health-banner-sub');
  const cfg = {
    healthy:  { cls: 'healthy',  text: 'System Healthy',  sub: 'All destinations delivering normally' },
    degraded: { cls: 'degraded', text: 'System Degraded', sub: 'Some destinations experiencing failures' },
    critical: { cls: 'critical', text: 'System Critical', sub: 'Immediate attention required' },
  };
  const c = cfg[status] || cfg.healthy;
  banner.className = 'health-banner ' + c.cls;
  if (title) title.textContent = c.text;
  if (sub) sub.textContent = c.sub;
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
    circuitSub.textContent = 'Paused: ' + kpis.open_destinations.slice(0, 2).join(', ');
    circuitSub.style.color = 'var(--danger)';
  } else {
    circuitSub.textContent = 'all delivering normally';
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
  if (!el) return;
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

// ---------------------------------------------------------------------------
// Reliability Command Center
// ---------------------------------------------------------------------------
async function _renderCommandCenter(kpis, incidents) {
  let score = _computeDerivedScore(kpis, incidents);
  let statusText = score >= 90 ? 'Healthy' : score >= 70 ? 'Degraded' : 'Critical';
  try {
    const hd = await apiFetch('/dlq/health');
    if (typeof hd.overall_score === 'number') score = hd.overall_score;
    if (hd.health_status) {
      statusText = hd.health_status.charAt(0) + hd.health_status.slice(1).toLowerCase();
    }
  } catch {}

  const scoreEl = document.getElementById('cc-score-num');
  if (!scoreEl) return;

  const color = score >= 90 ? 'var(--success)' : score >= 70 ? 'var(--warn)' : 'var(--danger)';
  const dotCls = score >= 90 ? 'cc-dot--ok' : score >= 70 ? 'cc-dot--warn' : 'cc-dot--err';

  scoreEl.textContent = score;
  scoreEl.style.color = color;

  const barEl = document.getElementById('cc-bar-fill');
  if (barEl) { barEl.style.width = score + '%'; barEl.style.background = color; }

  const dotEl = document.getElementById('cc-dot');
  if (dotEl) dotEl.className = 'cc-dot ' + dotCls;

  const statusEl = document.getElementById('cc-status-text');
  if (statusEl) statusEl.textContent = statusText;

  const openIncidents = incidents.filter(i => i.state === 'OPEN').length;
  const cbOpen = (kpis.circuit_breakers?.open ?? 0) + (kpis.circuit_breakers?.half_open ?? 0);
  const dlq = kpis.dlq_depth ?? 0;
  const healthyDests = destinations.filter(d => d.circuit_state === 'closed' && d.is_enabled !== false).length;

  const setNum = (id, val, warn) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = String(val);
    el.style.color = warn ? 'var(--danger)' : '';
  };
  setNum('cc-healthy-dests', healthyDests);
  setNum('cc-open-incidents', openIncidents, openIncidents > 0);
  setNum('cc-dlq-depth', dlq, dlq > 0);
  setNum('cc-cb-open', cbOpen, cbOpen > 0);
}

function _computeDerivedScore(kpis, incidents) {
  let score = 100;
  const rate = kpis.success_rate_24h ?? 100;
  if (rate < 100) score -= Math.min(40, (100 - rate) * 2);
  const dlq = kpis.dlq_depth ?? 0;
  if (dlq > 0) score -= Math.min(20, dlq * 2);
  const cbOpen = (kpis.circuit_breakers?.open ?? 0);
  score -= cbOpen * 8;
  const openInc = incidents.filter(i => i.state === 'OPEN').length;
  score -= openInc * 3;
  return Math.max(0, Math.round(score));
}

// ---------------------------------------------------------------------------
// Reliability Feed
// ---------------------------------------------------------------------------
function _pushFeedEvent(cls, msg, dest, ts) {
  feedEvents.unshift({ cls, msg, dest: dest || '', ts: ts || new Date().toISOString() });
  if (feedEvents.length > FEED_MAX) feedEvents.length = FEED_MAX;
  _renderFeed(true);
}

function _renderFeed(newEventAtTop) {
  const body = document.getElementById('mc-stream-body');
  if (!body) return;
  if (!feedEvents.length) {
    const hasDest = destinations.length > 0;
    if (!hasDest) {
      body.innerHTML = `
        <div style="display:flex;flex-direction:column;align-items:center;text-align:center;padding:32px 24px;gap:14px">
          <div style="width:44px;height:44px;border-radius:50%;background:rgba(0,212,126,.08);border:1px solid rgba(0,212,126,.2);display:flex;align-items:center;justify-content:center;color:#00D47E;flex-shrink:0">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><circle cx="12" cy="12" r="9"/><line x1="12" y1="3" x2="12" y2="9"/><line x1="12" y1="15" x2="12" y2="21"/><line x1="3" y1="12" x2="9" y2="12"/><line x1="15" y1="12" x2="21" y2="12"/></svg>
          </div>
          <div>
            <div style="font-size:13.5px;font-weight:600;color:var(--text);margin-bottom:5px">Add a destination first</div>
            <div style="font-size:12px;color:var(--text3);line-height:1.6;max-width:220px">Tell Relora where to forward events — your app's URL, a webhook endpoint, anywhere.</div>
          </div>
          <button class="btn btn-primary btn-sm" onclick="openDestWizard()" style="gap:6px">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 5v14M5 12h14"/></svg>
            Add destination
          </button>
          <a class="btn btn-ghost btn-sm" href="/index.html" target="_blank" style="font-size:11px;color:var(--text3)">Watch the demo →</a>
        </div>`;
    } else {
      const ingestUrl = window.location.origin + '/api/v1/ingest';
      const apiKey = currentProject?.api_key || 'YOUR_API_KEY';
      body.innerHTML = `
        <div style="padding:20px 16px;display:flex;flex-direction:column;gap:14px">
          <div>
            <div style="font-size:12px;font-weight:600;color:var(--text2);margin-bottom:6px">Your ingest URL</div>
            <div style="display:flex;align-items:center;gap:8px">
              <input readonly style="flex:1;font-family:var(--mono);font-size:11px;background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:6px 10px;color:var(--text2);outline:none;min-width:0" value="${esc(ingestUrl)}" onclick="this.select()">
              <button class="btn btn-secondary btn-sm" style="flex-shrink:0;font-size:11px" onclick="navigator.clipboard.writeText('${esc(ingestUrl)}').then(()=>toast('URL copied','success'))">Copy</button>
            </div>
          </div>
          <div>
            <div style="font-size:12px;font-weight:600;color:var(--text2);margin-bottom:6px">Quick test</div>
            <div style="position:relative">
              <pre id="feed-empty-curl" style="font-family:var(--mono);font-size:10.5px;line-height:1.7;color:var(--text3);background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:10px 40px 10px 12px;margin:0;white-space:pre-wrap;word-break:break-all">curl -X POST ${esc(ingestUrl)} \\
  -H "X-Relora-API-Key: ${esc(apiKey)}" \\
  -H "Content-Type: application/json" \\
  -d '{"event_type":"test.ping","data":{}}'</pre>
              <button class="btn btn-ghost" style="position:absolute;top:6px;right:6px;font-size:10px;padding:3px 7px;border:1px solid var(--border);border-radius:5px;color:var(--text3)" onclick="_nraCopyCmd(document.getElementById('feed-empty-curl').textContent)">Copy</button>
            </div>
          </div>
          <div style="font-size:11.5px;color:var(--text3)">Events appear here live once they start flowing.</div>
        </div>`;
    }
    return;
  }

  const typeMap = { ok: 'DELIVERED', err: 'FAILED', warn: 'INCIDENT', info: 'INFO' };
  const clsMap  = { ok: 'ev-ok',    err: 'ev-err',  warn: 'ev-warn',  info: 'ev-info' };

  body.innerHTML = feedEvents.map((e, i) => {
    const t = new Date(e.ts);
    const hh = t.getHours().toString().padStart(2, '0');
    const mm = t.getMinutes().toString().padStart(2, '0');
    const ss = t.getSeconds().toString().padStart(2, '0');
    const timeStr = `${hh}:${mm}:${ss}`;
    const type = typeMap[e.cls] || 'EVENT';
    const cls  = clsMap[e.cls]  || 'ev-info';
    const isNew = newEventAtTop && i === 0;
    return `<div class="mc-event${isNew ? ' mc-event--new' : ''}">
      <span class="mc-event-time">${timeStr}</span>
      <span class="mc-event-type ${cls}">${type}</span>
      <div class="mc-event-body">
        <span class="mc-event-what">${esc(e.msg)}</span>
        ${e.dest ? `<span class="mc-event-arr">→</span><span class="mc-event-where">${esc(e.dest)}</span>` : ''}
      </div>
    </div>`;
  }).join('');
}

// ---------------------------------------------------------------------------
// Architecture Visualization
// ---------------------------------------------------------------------------
function _arcVizPulse(cls) {
  ['arch-conn-1', 'arch-conn-2', 'arch-conn-3'].forEach((id, i) => {
    setTimeout(() => {
      const conn = document.getElementById(id);
      if (!conn) return;
      const p = document.createElement('div');
      p.className = `arch-particle arch-particle--${cls}`;
      conn.appendChild(p);
      setTimeout(() => p.remove(), 800);
    }, i * 180);
  });
}

function _renderArchViz(kpis) {
  const rate       = kpis?.success_rate_24h;
  const rateColor  = rate == null ? '' : rate >= 99 ? 'var(--success)' : rate >= 95 ? 'var(--warn)' : 'var(--danger)';
  const pending    = _pendingQueue;
  const healthyDests = destinations.filter(d => d.circuit_state === 'closed' && d.is_enabled !== false).length;

  const ingestSub = document.getElementById('arch-ingest-sub');
  const workerSub = document.getElementById('arch-worker-sub');
  const destSub   = document.getElementById('arch-dest-sub');

  if (ingestSub) {
    ingestSub.textContent = rate != null ? rate.toFixed(1) + '% success' : '—';
    ingestSub.style.color = rateColor;
  }
  if (workerSub) {
    workerSub.textContent = pending.toLocaleString() + ' pending';
    workerSub.style.color = pending > 50 ? 'var(--warn)' : '';
  }
  if (destSub) {
    destSub.textContent   = destinations.length ? `${healthyDests} / ${destinations.length} healthy` : '—';
    destSub.style.color   = healthyDests < destinations.length && destinations.length > 0 ? 'var(--warn)' : '';
  }
}

// ---------------------------------------------------------------------------
// What Changed?
// ---------------------------------------------------------------------------
function _renderWhatChanged(kpis, incidents) {
  const card = document.getElementById('what-changed-card');
  const body = document.getElementById('wc-body');
  if (!card || !body) return;

  const rate    = kpis?.success_rate_24h ?? 100;
  const dlq     = kpis?.dlq_depth ?? 0;
  const openInc = (incidents || []).filter(i => i.state === 'OPEN');

  if (rate >= 99 && dlq === 0 && openInc.length === 0) {
    card.style.display = 'none';
    return;
  }

  let html = '';
  if (rate < 99) {
    const cls = rate >= 95 ? 'warn' : 'bad';
    html += `<div class="wc-metric">
      <span class="wc-label">Success Rate</span>
      <div class="wc-delta">
        <span class="wc-before">100%</span>
        <span class="wc-arrow">→</span>
        <span class="wc-after wc-after--${cls}">${rate.toFixed(1)}%</span>
      </div>
    </div>`;
  }
  if (openInc.length > 0) {
    const top   = openInc[0];
    const cause = [top.category, top.subcategory].filter(Boolean).join(' · ');
    const dest  = top.destination_name ? ' on <strong>' + esc(top.destination_name) + '</strong>' : '';
    html += `<div class="wc-cause">Most likely cause: <strong>${esc(cause)}</strong>${dest}</div>`;
  } else if (dlq > 0) {
    html += `<div class="wc-cause">DLQ depth: <strong>${dlq} failed webhook${dlq !== 1 ? 's' : ''} awaiting review</strong>
      — <a href="#" onclick="navTo('dlq-intelligence');return false;" style="color:var(--accent-light)">investigate →</a></div>`;
  }

  body.innerHTML = html;
  card.style.display = 'flex';
}

function _syncFeedFromDashboard(failures, incidents, recentActivity) {
  const seen = new Set(feedEvents.map(e => e._id).filter(Boolean));
  const newEvents = [];

  // Seed from recent_activity — mix of completed + failed deliveries
  (recentActivity || []).forEach(a => {
    if (seen.has(a.id)) return;
    seen.add(a.id);
    const dest = a.destination_name || _shortUrl(a.destination_url);
    const cls  = a.status === 'completed' ? 'ok' : 'err';
    const msg  = a.status === 'completed' ? 'Delivery succeeded' : 'Delivery failed';
    newEvents.push({ cls, msg, dest, ts: a.updated_at, _id: a.id });
  });

  // Layer in any failures not already covered by recent_activity
  failures.slice(0, 10).forEach(f => {
    if (seen.has(f.id)) return;
    seen.add(f.id);
    const dest = f.destination_name || _shortUrl(f.destination_url);
    const cat  = (f.failure_category || 'unknown').replace(/_/g, ' ').toLowerCase();
    newEvents.push({ cls: 'err', msg: 'Delivery failed · ' + cat, dest, ts: f.updated_at, _id: f.id });
  });

  // Open incidents
  incidents.filter(i => i.state === 'OPEN').slice(0, 5).forEach(i => {
    const key = 'inc-' + i.id;
    if (seen.has(key)) return;
    seen.add(key);
    const cat = (i.category || 'Unknown').toLowerCase().replace(/_/g, ' ');
    newEvents.push({ cls: 'warn', msg: 'Incident open · ' + cat, dest: i.destination_name || '', ts: i.last_seen_at, _id: key });
  });

  const merged = [...newEvents, ...feedEvents];
  merged.sort((a, b) => new Date(b.ts) - new Date(a.ts));
  feedEvents.length = 0;
  merged.slice(0, FEED_MAX).forEach(e => feedEvents.push(e));
  _renderFeed(false);
}

function _shortUrl(url) {
  try { const u = new URL(url); return u.hostname; } catch { return url || '—'; }
}

// ---------------------------------------------------------------------------
// Mission Control Renderers
// ---------------------------------------------------------------------------

function _renderKPIStrip(kpis, incidents, recentFailures) {
  const rate          = kpis?.success_rate_24h ?? null;
  const delivered     = _mcKpis?.deliveries_today ?? 0;
  const dlq           = kpis?.dlq_depth ?? 0;
  const p95           = kpis?.p95_latency_ms ?? null;
  const openIncidents = (incidents || []).filter(i => i.state === 'OPEN').length;
  const failures      = (recentFailures || []).length;
  const totalDests    = destinations.length;
  const healthyDests  = destinations.filter(d => d.circuit_state === 'closed' && d.is_enabled !== false).length;

  const setKpi = (numId, val, cls, cellId, alertCell) => {
    const el = document.getElementById(numId);
    if (el) {
      el.textContent = val;
      el.className = 'mc-kpi-num' + (cls ? ' mc-kpi-num--' + cls : '');
    }
    if (cellId) {
      const cell = document.getElementById(cellId);
      if (cell) cell.classList.toggle('mc-kpi-cell--alert', !!alertCell);
    }
  };

  const noActivity = delivered === 0 && failures === 0;

  if (rate !== null && !noActivity) {
    const cls = rate >= 99 ? 'good' : rate >= 95 ? 'warn' : 'bad';
    setKpi('mc-kpi-rate', rate.toFixed(1) + '%', cls, 'mc-kpi-cell-rate', rate < 95);
  }

  setKpi('mc-kpi-delivered',
    delivered > 0 ? delivered.toLocaleString() : '0',
    delivered > 0 ? 'good' : '',
    'mc-kpi-cell-delivered', false);

  setKpi('mc-kpi-failures',
    failures > 0 ? failures : '0',
    failures > 0 ? 'bad' : noActivity ? '' : 'good',
    'mc-kpi-cell-failures', failures > 0);

  setKpi('mc-kpi-dlq-strip',
    dlq > 0 ? dlq.toLocaleString() : '0',
    dlq > 0 ? (dlq >= 10 ? 'bad' : 'warn') : 'good',
    'mc-kpi-cell-dlq', dlq > 0);

  setKpi('mc-kpi-incidents',
    openIncidents > 0 ? openIncidents : '0',
    openIncidents > 0 ? 'bad' : 'good',
    'mc-kpi-cell-incidents', openIncidents > 0);

  if (p95 !== null) {
    setKpi('mc-kpi-p95-strip',
      p95 > 0 ? p95 + ' ms' : '—',
      p95 > 5000 ? 'bad' : p95 > 2000 ? 'warn' : '',
      'mc-kpi-cell-p95', p95 > 5000);
  }

  if (totalDests > 0) {
    const destBad = healthyDests < totalDests;
    setKpi('mc-kpi-dests',
      healthyDests + '/' + totalDests,
      destBad ? 'warn' : 'good',
      'mc-kpi-cell-dests', destBad);
  } else {
    setKpi('mc-kpi-dests', '0', '', 'mc-kpi-cell-dests', false);
  }

  // Hide the strip when there's no activity — dashes/zeros convey nothing useful
  const strip = document.querySelector('.mc-kpi-strip');
  if (strip) strip.style.display = noActivity ? 'none' : '';
}

function _renderMCBanner() {
  const banner = document.getElementById('mc-banner');
  if (!banner) return;

  const refreshBtn = `<div class="mc-banner-right">
    <button class="mc-btn-bare" onclick="loadDashboard()">
      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.12"/></svg>
      refresh
    </button>
  </div>`;

  // ── Setup mode: replace banner with horizontal 3-step progress strip ──────
  if (_onboardingProgress && !_onboardingProgress.activated) {
    const steps = _onboardingProgress.steps;
    const currentStep = steps.find(s => !s.completed);

    const checkSvg = `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`;

    const pills = steps.map((s, i) => {
      if (s.completed) {
        return `<div class="mc-ob-step mc-ob-step--done">${checkSvg}<span>${esc(s.title)}</span></div>`;
      }
      const active = currentStep && s.id === currentStep.id;
      const cta = active ? _obStepCTA(s.id) : '';
      const cls = active ? 'mc-ob-step--active' : 'mc-ob-step--pending';
      return `<div class="mc-ob-step ${cls}"><span class="mc-ob-num">${i + 1}</span><span>${esc(s.title)}</span>${cta}</div>`;
    });

    const strip = pills.map((p, i) => i === 0 ? p : `<span class="mc-ob-arr">→</span>${p}`).join('');
    banner.className = 'mc-banner mc-banner--setup';
    banner.innerHTML = `<div class="mc-ob-strip">${strip}</div>${refreshBtn}`;
    return;
  }

  // ── Operational mode ──────────────────────────────────────────────────────
  const kpis            = _mcKpis || {};
  const status          = _mcSystemStatus || 'healthy';
  const dlq             = kpis.dlq_depth ?? 0;
  const cbOpen          = (kpis.circuit_breakers?.open ?? 0) + (kpis.circuit_breakers?.half_open ?? 0);
  const rate            = kpis.success_rate_24h ?? 100;
  const deliveriesToday = kpis.deliveries_today ?? 0;
  const lastDelivery    = kpis.last_delivery_at || null;

  let level, mainText;
  if (status === 'critical' || cbOpen > 0) {
    level    = 'crit';
    mainText = cbOpen > 0
      ? `${cbOpen} circuit breaker${cbOpen > 1 ? 's' : ''} open — deliveries suspended`
      : 'System critical';
  } else if (status === 'degraded' || dlq > 0 || rate < 99) {
    level    = 'warn';
    mainText = rate < 99
      ? `Delivery rate degraded — ${rate.toFixed(1)}%`
      : dlq > 0
        ? `${dlq} event${dlq !== 1 ? 's' : ''} in dead letter queue`
        : 'System degraded';
  } else {
    level = 'ok';
    mainText = destinations.length > 0 ? 'All systems operational' : 'Ready to route events';
  }

  const metaParts = [];
  if (lastDelivery) {
    const secs = Math.floor((Date.now() - new Date(lastDelivery)) / 1000);
    const lastStr = secs < 60 ? `${secs}s ago` : relTime(lastDelivery);
    metaParts.push(`<span class="mc-meta-item">Last delivery: ${lastStr}</span>`);
  } else if (destinations.length > 0) {
    metaParts.push(`<span class="mc-meta-item mc-meta-muted">No deliveries yet</span>`);
  }
  if (deliveriesToday > 0) {
    metaParts.push(`<span class="mc-meta-item">${deliveriesToday.toLocaleString()} delivered today</span>`);
  }
  if (!_mcWorkerOk) {
    metaParts.push(`<span class="mc-meta-item meta-crit">worker offline</span>`);
  }
  if (destinations.length > 0) {
    const healthyDests = destinations.filter(d => d.circuit_state === 'closed' && d.is_enabled !== false).length;
    if (healthyDests < destinations.length) {
      const n = destinations.length - healthyDests;
      metaParts.push(`<span class="mc-meta-item meta-warn">${n} destination${n > 1 ? 's' : ''} degraded</span>`);
    }
  }
  if (_mcPending > 50) {
    metaParts.push(`<span class="mc-meta-item meta-warn">${_mcPending.toLocaleString()} pending</span>`);
  }

  banner.className = 'mc-banner banner-' + level;
  banner.innerHTML = `
    <div class="mc-banner-signal">
      <div class="mc-signal-dot ${level}" id="mc-signal-dot"></div>
      <span class="mc-signal-text text-${level}" id="mc-signal-text">${esc(mainText)}</span>
    </div>
    <div class="mc-banner-meta" id="mc-banner-meta">${metaParts.join('')}</div>
    ${refreshBtn}`;
}

function _obStepCTA(stepId) {
  if (stepId === 1) return `<button class="mc-ob-cta" onclick="openDestWizard()">Add Destination →</button>`;
  if (stepId === 2) return '';
  return '';
}

function _renderMCTriage(kpis, incidents, sloBreaches, schemaDrift) {
  const triage = document.getElementById('mc-triage');
  const list   = document.getElementById('mc-triage-list');
  if (!triage || !list) return;

  const items  = [];
  const dlq    = kpis?.dlq_depth ?? 0;
  const cbOpen = kpis?.circuit_breakers?.open ?? 0;
  const openInc = (incidents || []).filter(i => i.state === 'OPEN');

  openInc.forEach(i => {
    const dest    = i.destination_name || 'a destination';
    const count   = i.affected_count ?? 0;
    const countTxt = count > 0 ? ` — ${count} event${count !== 1 ? 's' : ''} affected` : '';
    items.push({
      sev: 'crit',
      html: `<strong>${esc(dest)}</strong> is failing deliveries${countTxt}`,
      action: { label: 'Review →', fn: `navTo('dlq-intelligence')` },
    });
  });

  if (cbOpen > 0) {
    const openDests = kpis.open_destinations?.length ? kpis.open_destinations : [];
    if (openDests.length) {
      openDests.forEach(d => items.push({
        sev: 'crit',
        html: `<strong>${esc(d)}</strong> has stopped responding — deliveries paused`,
        action: { label: 'Fix it →', fn: `navTo('destinations')` },
      }));
    } else {
      items.push({
        sev: 'crit',
        html: `${cbOpen} destination${cbOpen > 1 ? 's have' : ' has'} stopped responding — deliveries paused`,
        action: { label: 'Fix it →', fn: `navTo('destinations')` },
      });
    }
  }

  if (dlq > 0) {
    items.push({
      sev: 'warn',
      html: `<strong>${dlq} event${dlq !== 1 ? 's' : ''}</strong> could not be delivered after all retry attempts`,
      action: { label: 'Review →', fn: `navTo('dlq-intelligence')` },
    });
  }

  (sloBreaches || []).forEach(b => {
    items.push({
      sev: 'warn',
      html: `<strong>${esc(b.destination_name)}</strong> is below its reliability goal — ${b.current_pct.toFixed(1)}% delivered (goal: ${b.target_pct}%)`,
      action: { label: 'View trends →', fn: `navTo('analytics')` },
    });
  });

  if (schemaDrift > 0) {
    items.push({
      sev: 'warn',
      html: `Your source started sending a different payload shape — ${schemaDrift} change${schemaDrift > 1 ? 's' : ''} detected. No deliveries were affected.`,
      action: null,
    });
  }

  const critSvg = `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;
  const warnSvg = `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`;
  const checkSvg = `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`;

  const hdr = triage.querySelector('.mc-triage-hdr');

  if (!items.length) {
    triage.style.display = '';
    triage.classList.add('mc-triage--ok');
    if (hdr) hdr.innerHTML = `${checkSvg} Everything looks good`;
    list.innerHTML = `
      <div class="mc-triage-item">
        <div class="mc-triage-text mc-triage-text--muted" style="padding-left:4px">No issues to review. Your deliveries are running normally.</div>
      </div>`;
    return;
  }

  triage.classList.remove('mc-triage--ok');
  triage.style.display = '';
  if (hdr) hdr.innerHTML = `${warnSvg} Needs attention`;
  list.innerHTML = items.map(item => `
    <div class="mc-triage-item">
      <div class="mc-triage-icon mc-triage-icon--${item.sev}">${item.sev === 'crit' ? critSvg : warnSvg}</div>
      <div class="mc-triage-text">${item.html}</div>
      ${item.action ? `<button class="mc-triage-action" onclick="${item.action.fn}">${item.action.label}</button>` : ''}
    </div>`).join('');
}

function _renderMCDLQ(dlq) {
  const block = document.getElementById('mc-dlq-block');
  const numEl = document.getElementById('mc-dlq-num');
  if (!block) return;
  block.style.display = dlq > 0 ? '' : 'none';
  if (numEl && dlq > 0) numEl.textContent = dlq.toLocaleString();
  // Keep global depth in sync with dashboard data
  if (dlq !== _dlqDepth) { _dlqDepth = dlq; _updateDLQIndicators(dlq); }
}

function _renderMCDestinations() {
  const el = document.getElementById('mc-dest-list');
  if (!el) return;
  // Keep the KPI strip destinations cell current
  const totalDests   = destinations.length;
  const healthyDests = destinations.filter(d => d.circuit_state === 'closed' && d.is_enabled !== false).length;
  const numEl = document.getElementById('mc-kpi-dests');
  const cellEl = document.getElementById('mc-kpi-cell-dests');
  if (numEl) {
    numEl.textContent = totalDests > 0 ? healthyDests + '/' + totalDests : '0';
    numEl.className = 'mc-kpi-num' + (totalDests === 0 ? '' : healthyDests < totalDests ? ' mc-kpi-num--warn' : ' mc-kpi-num--good');
  }
  if (cellEl) cellEl.classList.toggle('mc-kpi-cell--alert', totalDests > 0 && healthyDests < totalDests);
  if (!destinations.length) {
    el.innerHTML = `
      <div style="padding:14px 14px 12px;display:flex;flex-direction:column;gap:10px">
        <div style="font-size:12px;color:var(--text3);line-height:1.55">Where should Relora send your events? Add an endpoint and deliveries start immediately.</div>
        <button class="btn btn-primary btn-sm" style="width:100%;justify-content:center;gap:6px" onclick="openDestWizard()">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 5v14M5 12h14"/></svg>
          Add destination
        </button>
      </div>`;
    return;
  }

  const statusCls = d => {
    if (!d.is_enabled) return 'disabled';
    if (d.circuit_state === 'open') return 'unhealthy';
    if (d.circuit_state === 'half_open') return 'degraded';
    return 'healthy';
  };

  el.innerHTML = destinations.map(d => {
    const dot  = statusCls(d);
    const name = d.name || _shortUrl(d.url);
    const circuit = d.circuit_state || 'closed';
    const rateEl = document.getElementById(`dest-rate-${d.id}`);
    const rateText = rateEl?.textContent || '—';
    const ratePct = parseFloat(rateText);
    const rateCls = isNaN(ratePct) ? '' : ratePct >= 99 ? '' : ratePct >= 95 ? 'rate-warn' : 'rate-bad';
    return `<div class="mc-dest-row" onclick="navTo('destinations')">
      <div class="mc-dest-dot ${dot}"></div>
      <span class="mc-dest-name">${esc(name)}</span>
      <span class="mc-dest-rate ${rateCls}" id="mc-dr-${d.id}">${rateText}</span>
      <span class="mc-dest-cb circuit-${circuit}">${circuit.replace('_', '·').toUpperCase()}</span>
    </div>`;
  }).join('');
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
  if (!_showTestEvents) qs += `&exclude_simulations=true`;
  if (destId) qs += `&destination_id=${destId}`;

  try {
    const data = await apiFetch('/webhooks' + qs);
    renderWebhooksTable(data, document.getElementById('webhooks-table-body'));
    renderPagination('wh-pagination', data.page, data.total_pages, loadWebhooks);
  } catch (e) { toast(e.message, 'error'); }
}

function toggleTestEvents() {
  _showTestEvents = !_showTestEvents;
  const btn = document.getElementById('btn-toggle-test');
  const clearBtn = document.getElementById('btn-clear-test');
  if (btn) {
    btn.textContent = _showTestEvents ? 'Hide test events' : 'Show test events';
    btn.style.color = _showTestEvents ? 'var(--accent)' : 'var(--text3)';
  }
  if (clearBtn) clearBtn.style.display = _showTestEvents ? '' : 'none';
  loadWebhooks(1);
}

async function clearTestEvents() {
  if (!confirm('Delete all test events created by the simulator? This cannot be undone.')) return;
  try {
    const r = await apiFetch('/webhooks/simulated', { method: 'DELETE' });
    const count = r?.deleted ?? 0;
    toast(`Cleared ${count} test event${count !== 1 ? 's' : ''}`, 'success');
    loadWebhooks(1);
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
      <td><span class="badge ${w.status}">${w.status}</span>${w.is_simulation ? ' <span style="font-size:9px;font-weight:600;letter-spacing:.04em;border:1px solid var(--text3);color:var(--text3);border-radius:3px;padding:1px 4px;vertical-align:middle">TEST</span>' : ''}</td>
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

const debounceSearch = debounce(() => loadWebhooks(1), 400);

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
    replay.style.display = (w.status === 'failed' || w.status === 'dead') ? 'inline-flex' : 'none';

    // Timeline
    const timeline = document.getElementById('panel-timeline');
    if (!w.attempts?.length) {
      timeline.innerHTML = '<div style="font-size:12px;color:var(--text3)">No attempts yet</div>';
    } else {
      timeline.innerHTML = w.attempts.map(a => {
        const ok = a.status_code >= 200 && a.status_code < 300;
        const cls = ok ? 'success' : 'failed';
        const cat = a.failure_category;
        const catColor = {
          TIMEOUT: 'var(--warn)', NETWORK: 'var(--warn)', SERVER_ERROR: 'var(--info)',
          AUTHENTICATION: 'var(--danger)', AUTHORIZATION: 'var(--danger)',
          RATE_LIMITING: 'var(--accent-light)', CLIENT_ERROR: 'var(--text2)',
          DNS: 'var(--warn)', SSL: 'var(--danger)', CIRCUIT_BREAKER: 'var(--danger)',
        }[cat] || 'var(--text3)';
        const catBadge = cat && !ok ? `<span style="font-size:10px;border:1px solid ${catColor};color:${catColor};border-radius:4px;padding:1px 5px;margin-left:6px">${cat.replace(/_/g,' ')}</span>` : '';
        const advice = cat && !ok ? _failureCategoryAdvice(cat) : '';
        return `
          <div class="timeline-item">
            <div class="timeline-dot ${cls}">${a.attempt_number}</div>
            <div class="timeline-content">
              <div class="timeline-title">
                ${ok ? '✅' : '❌'} Attempt ${a.attempt_number}
                ${a.status_code ? `<span class="inline-code">${a.status_code}</span>` : ''}
                ${a.duration_ms ? `<span style="color:var(--text3);font-size:11px">${a.duration_ms}ms</span>` : ''}
                ${catBadge}
              </div>
              <div class="timeline-meta">${a.attempted_at ? relTime(a.attempted_at) : ''}${a.retry_strategy_used ? ` · ${a.retry_strategy_used}` : ''}</div>
              ${a.error_message ? `<div class="timeline-error">${esc(a.error_message)}</div>` : ''}
              ${advice ? `<div style="margin-top:5px;font-size:11px;color:var(--text3);line-height:1.4">${advice}</div>` : ''}
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
    window._reloraHasRealDestinations = destinations.some(
      d => d.is_sandbox !== true && !d.url?.includes('/api/v1/sandbox/inbox')
    );
    renderDestTable();
    populateDestSelects();
    _loadDestRates();
    _renderSuggestionBar();
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
    tbody.innerHTML = `<tr><td colspan="7"><div class="empty-state">
      <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.25"><circle cx="12" cy="12" r="3"/><circle cx="12" cy="12" r="9"/></svg>
      <div class="title">No destinations yet</div>
      <div class="desc">Add a destination to start routing webhooks to your endpoints.</div>
    </div></td></tr>`;
    return;
  }

  const circuitCssClass = s => s === 'closed' ? 'CLOSED' : s === 'half_open' ? 'HALF_OPEN' : 'OPEN';
  const circuitLabel    = s => ({ closed: 'Healthy', half_open: 'Recovering', open: 'Suspended' })[s] || s;
  const statusClass  = d => {
    if (d.circuit_state === 'open') return 'unhealthy';
    if (d.circuit_state === 'half_open') return 'degraded';
    return 'healthy';
  };
  const statusLabel  = d => {
    if (!d.is_enabled) return 'Disabled';
    if (d.circuit_state === 'open') return 'Suspended';
    if (d.circuit_state === 'half_open') return 'Recovering';
    return 'Healthy';
  };
  const circuitTooltip = s => ({
    closed:    'Healthy — deliveries are passing through normally.',
    half_open: 'Recovering — this destination recently failed. Relora is sending test deliveries to check if it has recovered. No action needed.',
    open:      'Suspended — too many consecutive failures. Deliveries to this destination are paused. Fix the destination issue, then click Reset.',
  }[s] || '');

  tbody.innerHTML = destinations.map(d => {
    const isOpen = d.circuit_state === 'open' || d.circuit_state === 'half_open';
    const sandboxBadge = d.is_sandbox
      ? `<span class="dest-sandbox-badge" title="Auto-created sandbox for onboarding">Sandbox</span>`
      : '';
    return `
    <tr>
      <td><span class="dest-status-dot ${statusClass(d)}">${statusLabel(d)}</span></td>
      <td style="font-weight:500;color:var(--text)">${esc(d.name)}${sandboxBadge}</td>
      <td class="cell-url" title="${esc(d.url)}">${esc(d.url)}</td>
      <td style="font-family:var(--mono);font-size:12px">0 / ${d.max_retries}</td>
      <td>
        <span class="circuit-text ${circuitCssClass(d.circuit_state)}" title="${circuitTooltip(d.circuit_state)}" style="cursor:help">${circuitLabel(d.circuit_state)}</span>
        ${isOpen ? `<button class="btn btn-xs btn-secondary" style="margin-left:6px" title="Force circuit back to CLOSED" onclick="resetCircuitBreaker('${d.id}')">Reset</button>` : ''}
      </td>
      <td id="dest-rate-${d.id}" style="font-family:var(--mono);font-size:12px;color:var(--text3)">—</td>
      <td>
        <div class="dest-row-actions">
          <button class="btn btn-xs btn-secondary" title="Ping the endpoint to verify it responds" onclick="testDestination('${d.id}')">Ping</button>
          <button class="btn btn-xs btn-secondary" onclick="viewDestSla('${d.id}','${esc(d.name)}')">SLA</button>
          <button class="btn btn-xs btn-secondary" onclick="openDestModal(destinations.find(x=>x.id==='${d.id}'))">Edit</button>
          <button class="btn btn-xs btn-danger" onclick="deleteDestination('${d.id}')">Delete</button>
        </div>
      </td>
    </tr>
  `}).join('');
}

async function _loadDestRates() {
  if (!destinations.length) return;
  await Promise.allSettled(destinations.map(async d => {
    try {
      const s = await apiFetch(`/destinations/${d.id}/stats`);
      const cell = document.getElementById(`dest-rate-${d.id}`);
      if (!cell) return;
      const rate = s.success_rate ?? s.delivery_rate ?? null;
      if (rate === null) return;
      const pct = Math.round(rate * 100) / 100;
      const color = pct >= 99 ? 'var(--success)' : pct >= 95 ? 'var(--warn)' : 'var(--danger)';
      cell.textContent = pct + '%';
      cell.style.color = color;
    } catch {}
  }));
}

function populateDestSelects() {
  const blanks = { 'sim-destination': '<option value="">Select a destination…</option>' };
  ['wh-dest-filter', 'sim-destination', 'replay-dest'].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    const blank = blanks[id] || el.querySelector('option[value=""]')?.outerHTML || '<option value="">All</option>';
    el.innerHTML = blank + destinations.map(d => `<option value="${d.id}">${esc(d.name)}</option>`).join('');
  });
}

function openDestModal(existing = null, prefill = null) {
  _editingDestId = existing?.id || null;
  document.getElementById('dest-name').value = existing?.name || prefill?.name || '';
  document.getElementById('dest-url').value = existing?.url || prefill?.url || '';
  document.getElementById('dest-max-retries').value = existing?.max_retries ?? 5;
  document.getElementById('dest-backoff').value = existing?.backoff_base_seconds ?? 30;
  const destFilterEl = document.getElementById('dest-filter');
  const destFilterClear = document.getElementById('dest-filter-clear');
  destFilterEl.value = existing?.filter_expression || '';
  if (destFilterClear) destFilterClear.style.display = existing?.filter_expression ? '' : 'none';
  destFilterEl.oninput = () => { if (destFilterClear) destFilterClear.style.display = destFilterEl.value ? '' : 'none'; };
  document.getElementById('dest-ordering-key').value = existing?.ordering_key_field || '';
  document.getElementById('dest-secret').value = '';
  document.getElementById('dest-transform-type').value = existing?.transform_type || 'none';
  document.getElementById('dest-transform-map').value = existing?.transform_map ? JSON.stringify(existing.transform_map, null, 2) : '';
  document.getElementById('dest-transform-code').value = existing?.transform_code || '';
  document.getElementById('dest-modal-title').textContent = existing ? 'Edit Destination' : 'New Destination';
  _checkDestUrl(document.getElementById('dest-url').value);
  toggleTransformEditor();
  openModal('modal-dest');
}

// ---------------------------------------------------------------------------
// Destination type wizard — shown before the destination form for new users
// ---------------------------------------------------------------------------
function openDestWizard() {
  let overlay = document.getElementById('dest-wizard-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'dest-wizard-overlay';
    overlay.className = 'dest-wizard-overlay';
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.style.display = 'none'; });
    document.body.appendChild(overlay);
  }
  overlay.style.display = 'flex';
  overlay.innerHTML = `
    <div class="dest-wizard-box">
      <div class="dest-wizard-header">
        <span class="dest-wizard-title">What are you trying to do?</span>
        <button class="dest-wizard-close" onclick="document.getElementById('dest-wizard-overlay').style.display='none'">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
      </div>
      <div class="dest-wizard-grid">
        <button class="dest-wizard-opt" onclick="_pickDestType('try')">
          <div class="dest-wizard-icon">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75"><polygon points="5 3 19 12 5 21 5 3"/></svg>
          </div>
          <div class="dest-wizard-label">Just trying Relora</div>
          <div class="dest-wizard-hint">Send events to a temporary test URL</div>
        </button>
        <button class="dest-wizard-opt" onclick="_pickDestType('app')">
          <div class="dest-wizard-icon">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75"><rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2"/></svg>
          </div>
          <div class="dest-wizard-label">I have an application</div>
          <div class="dest-wizard-hint">Forward events to my backend API</div>
        </button>
        <button class="dest-wizard-opt" onclick="_pickDestType('local')">
          <div class="dest-wizard-icon">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
          </div>
          <div class="dest-wizard-label">Local development</div>
          <div class="dest-wizard-hint">Forward events to localhost while coding</div>
        </button>
        <button class="dest-wizard-opt" onclick="_pickDestType('custom')">
          <div class="dest-wizard-icon">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>
          </div>
          <div class="dest-wizard-label">Custom URL</div>
          <div class="dest-wizard-hint">I already have a destination in mind</div>
        </button>
      </div>
    </div>`;
}

function _pickDestType(type) {
  const overlay = document.getElementById('dest-wizard-overlay');
  if (overlay) overlay.style.display = 'none';
  const prefills = {
    try:    { name: 'Test Destination', url: 'https://webhook.site/' },
    app:    { name: 'My Application',   url: 'https://' },
    local:  { name: 'Local Dev',        url: 'http://host.docker.internal:3001/webhook' },
    custom: { name: '',                 url: '' },
  };
  openDestModal(null, prefills[type] || prefills.custom);
}

function _checkDestUrl(val) {
  const warn = document.getElementById('dest-url-warn');
  const suggest = document.getElementById('dest-url-suggest');
  if (!warn) return;
  const localhostMatch = val.match(/^(https?:\/\/)(localhost)(:\d+)/i);
  if (localhostMatch) {
    const fixed = val.replace(/localhost/, 'host.docker.internal');
    if (suggest) suggest.textContent = fixed;
    warn.style.display = '';
  } else {
    warn.style.display = 'none';
  }
}

function _applyDockerHost() {
  const input = document.getElementById('dest-url');
  if (!input) return;
  input.value = input.value.replace(/localhost/, 'host.docker.internal');
  _checkDestUrl(input.value);
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
    if (_editingDestId) {
      await apiFetch(`/destinations/${_editingDestId}`, { method: 'PUT', body: JSON.stringify(body) });
      closeModal('modal-dest');
      toast('Destination updated', 'success');
    } else {
      await apiFetch('/destinations', { method: 'POST', body: JSON.stringify(body) });
      closeModal('modal-dest');
      toast('Destination created', 'success');
    }
    _editingDestId = null;
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

async function sendTestEvent(id) {
  try {
    const r = await apiFetch(`/destinations/${id}/send-test-event`, { method: 'POST' });
    toast(`Test event queued for "${r.destination}" — watch the activity feed`, 'success');
  } catch (e) { toast(e.message, 'error'); }
}

async function clearTestData() {
  if (!confirm('Delete all test/simulated events? This cannot be undone.')) return;
  try {
    const r = await apiFetch('/data/test', { method: 'DELETE' });
    toast(r.message, 'success');
    await loadDashboard();
  } catch (e) { toast(e.message, 'error'); }
}

// ---------------------------------------------------------------------------
// Simulator
// ---------------------------------------------------------------------------
const _SIM_PROVIDERS = {
  stripe:  ['payment_intent.succeeded', 'payment_intent.payment_failed', 'customer.subscription.created', 'invoice.paid'],
  github:  ['push', 'pull_request.opened', 'issues.opened'],
  shopify: ['orders/create', 'orders/paid', 'customers/create'],
};

function openSimModal() {
  updateSimEventTypes();
  document.getElementById('sim-result').style.display = 'none';
  openModal('modal-sim');
}

function updateSimEventTypes() {
  const provider = document.getElementById('sim-provider').value;
  const events = _SIM_PROVIDERS[provider] || [];
  const sel = document.getElementById('sim-event-type');
  sel.innerHTML = events.map(e => `<option value="${e}">${e}</option>`).join('');
}

const _SIM_OUTCOME_LABELS = {
  real:    'Real delivery',
  success: 'Forced success (200)',
  fail:    'Forced fail (500 → DLQ)',
  flaky:   'Flaky (fails #1, recovers #2)',
};

async function runSimulate() {
  const destId    = document.getElementById('sim-destination').value;
  const provider  = document.getElementById('sim-provider').value;
  const eventType = document.getElementById('sim-event-type').value;
  const outcome   = document.getElementById('sim-outcome').value;
  const resultEl  = document.getElementById('sim-result');
  const btn       = document.getElementById('sim-send-btn');

  if (!destId) { toast('Select a destination first', 'error'); return; }

  btn.disabled = true;
  btn.textContent = 'Sending…';
  resultEl.style.display = 'none';

  try {
    const r = await apiFetch('/simulate', {
      method: 'POST',
      body: JSON.stringify({ provider, event_type: eventType, destination_id: destId, outcome }),
    });
    const label = _SIM_OUTCOME_LABELS[outcome] || outcome;
    resultEl.style.display = '';
    resultEl.style.color = 'var(--success)';
    resultEl.textContent = `Queued ${r.webhook_id} · ${provider}/${eventType} · ${label}`;
    const hint = outcome === 'fail'
      ? 'Test event sent — will exhaust retries and land in DLQ'
      : outcome === 'flaky'
        ? 'Test event sent — watch it fail once then recover in Webhooks'
        : 'Test event sent — check Webhooks tab';
    toast(hint, 'success');
    loadDashboard();
  } catch (e) {
    resultEl.style.display = '';
    resultEl.style.color = 'var(--danger)';
    resultEl.textContent = e.message;
    toast(e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="5 3 19 12 5 21 5 3"/></svg> Send';
  }
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
      el.innerHTML = `<div class="empty-state"><div class="icon">🔔</div><div class="title">No alerts yet</div><div class="desc">Connect Slack or email and Relora will notify you the moment deliveries start failing.</div></div>`;
      return;
    }
    el.innerHTML = alerts.map(a => {
      const triggers = [];
      if (a.dlq_threshold != null) triggers.push(`${a.dlq_threshold}+ failures`);
      if (a.error_rate_threshold != null) triggers.push(`success rate below ${a.error_rate_threshold}%`);
      const triggerText = triggers.length ? triggers.join(' or ') : 'any failure';
      const channelLabel = a.channel_type === 'slack' ? 'Slack' : 'Email';
      const statusLabel = a.enabled ? 'Active' : 'Paused';
      const statusColor = a.enabled ? 'color:var(--green)' : 'color:var(--text3)';
      return `
      <div class="alert-row">
        <div class="alert-icon">${a.channel_type === 'slack' ? '💬' : '📧'}</div>
        <div class="alert-info">
          <div class="alert-name">${esc(a.name)}</div>
          <div class="alert-type">via ${channelLabel} · <span style="${statusColor}">${statusLabel}</span> · fires when: ${esc(triggerText)}</div>
        </div>
        <div class="alert-actions">
          <button class="btn btn-xs btn-secondary" onclick="testAlert('${a.id}')">Test</button>
          <button class="btn btn-xs btn-danger" onclick="deleteAlert('${a.id}')">Delete</button>
        </div>
      </div>
    `}).join('');
  } catch {}
}

function openAlertModal() {
  document.getElementById('alert-name').value = '';
  document.getElementById('alert-slack-url').value = '';
  document.getElementById('alert-dlq-threshold').value = '';
  document.getElementById('alert-error-threshold').value = '';
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
  const dlqRaw = document.getElementById('alert-dlq-threshold').value;
  const errRaw = document.getElementById('alert-error-threshold').value;
  const body = {
    name: document.getElementById('alert-name').value,
    channel_type: type,
    config,
    dlq_threshold: dlqRaw ? parseInt(dlqRaw) : null,
    error_rate_threshold: errRaw ? parseFloat(errRaw) : null,
  };
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
// Bulk Replay
// ---------------------------------------------------------------------------
let _replayPollTimer = null;

function initReplay() {
  populateDestSelects();
  const now = new Date();
  const oneHourAgo = new Date(now - 3600000);
  document.getElementById('replay-from').value = toLocalDatetimeString(oneHourAgo);
  document.getElementById('replay-to').value = toLocalDatetimeString(now);
  loadReplayJobs();
}

async function loadReplayJobs() {
  const el = document.getElementById('replay-jobs-list');
  if (!el) return;
  try {
    const jobs = await apiFetch('/replay-jobs');
    _renderReplayJobs(jobs);
  } catch {}
}

function _renderReplayJobs(jobs) {
  const el = document.getElementById('replay-jobs-list');
  if (!el) return;
  if (!jobs.length) {
    el.innerHTML = `<div class="empty-state">
      <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.25"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.12"/></svg>
      <div class="title">No replay jobs yet</div>
      <div class="desc">Replay re-queues failed webhooks through the delivery pipeline at a controlled rate.</div>
    </div>`;
    return;
  }
  const statusColor = { pending: 'var(--text3)', running: 'var(--accent)', completed: 'var(--success)', failed: 'var(--danger)' };
  const statusBadge = s => `<span class="badge ${s === 'completed' ? 'completed' : s === 'failed' ? 'failed' : s === 'running' ? 'processing' : 'pending'}">${s}</span>`;
  el.innerHTML = `<div style="display:flex;flex-direction:column;gap:8px">` + jobs.map(j => {
    const pct = j.total_count > 0 ? Math.round(j.processed_count / j.total_count * 100) : 0;
    const isRunning = j.status === 'running' || j.status === 'pending';
    const destLabel = j.destination_id ? (destinations.find(d => d.id === j.destination_id)?.name || j.destination_id.substring(0,8) + '…') : 'All destinations';
    return `<div style="background:var(--surface2);border:1px solid var(--border);border-radius:var(--radius);padding:14px" id="replay-job-${j.id}">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
        <div style="font-weight:500;font-size:13px">Job <span class="inline-code">${j.id.substring(0,8)}…</span></div>
        ${statusBadge(j.status)}
      </div>
      <div style="font-size:12px;color:var(--text3);margin-bottom:${isRunning ? '10px' : '0'}">${j.total_count} webhooks · ${j.replay_rate_per_minute} evt/min · ${esc(destLabel)} · ${relTime(j.created_at)}</div>
      ${isRunning ? `
        <div style="background:var(--surface);border-radius:4px;height:6px;overflow:hidden;margin-bottom:6px">
          <div style="height:100%;width:${pct}%;background:var(--accent);transition:width .5s"></div>
        </div>
        <div style="font-size:11px;color:var(--text3)">${j.processed_count} / ${j.total_count} processed (${pct}%)</div>
      ` : j.status === 'completed' ? `
        <div style="font-size:12px;color:var(--success)">✓ ${j.processed_count} events replayed</div>
      ` : j.status === 'failed' ? `
        <div style="font-size:12px;color:var(--danger)">${esc(j.error_message || 'Job failed')}</div>
      ` : ''}
    </div>`;
  }).join('') + `</div>`;
}

let _activeReplayJobId = null;

async function startBulkReplay() {
  const from = document.getElementById('replay-from').value;
  const to = document.getElementById('replay-to').value;
  const destId = document.getElementById('replay-dest').value;
  const rate = parseInt(document.getElementById('replay-rate').value) || 100;
  if (!from || !to) { toast('Select a time window', 'error'); return; }

  const windowDays = (new Date(to) - new Date(from)) / (1000 * 60 * 60 * 24);
  if (windowDays > 1) {
    const days = windowDays.toFixed(1);
    if (!confirm(`Replay failed webhooks from the last ${days} days?\n\nThis will re-queue them for delivery at ${rate} events/min. Large windows may queue thousands of events.`)) return;
  }

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
    toast(`Replay job started — ${r.total_count} webhooks queued`, 'success');
    _activeReplayJobId = r.job_id;
    loadReplayJobs();
    _startReplayPoll(r.job_id);
  } catch (e) { toast(e.message, 'error'); }
}

function _startReplayPoll(jobId) {
  if (_replayPollTimer) clearInterval(_replayPollTimer);
  _replayPollTimer = setInterval(async () => {
    try {
      const job = await apiFetch(`/replay-jobs/${jobId}`);
      // Update the specific job row if visible
      loadReplayJobs();
      if (job.status === 'completed' || job.status === 'failed') {
        clearInterval(_replayPollTimer);
        _replayPollTimer = null;
        const msg = job.status === 'completed'
          ? `Replay complete — ${job.processed_count} events replayed`
          : `Replay job failed: ${job.error_message || 'unknown error'}`;
        toast(msg, job.status === 'completed' ? 'success' : 'error');
        loadDashboard();
      }
    } catch { clearInterval(_replayPollTimer); _replayPollTimer = null; }
  }, 3000);
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
    const isOwner = members.find(m => m.user_id === currentUser?.id)?.role === 'owner';
    el.innerHTML = `<table>
      <thead><tr><th>Email</th><th>Role</th><th>Joined</th><th style="width:160px"></th></tr></thead>
      <tbody>${members.map(m => {
        const isSelf = m.user_id === currentUser?.id;
        const roleSelect = isOwner && !isSelf
          ? `<select class="select" style="height:26px;font-size:11px;padding:2px 6px;width:90px" onchange="changeMemberRole('${m.user_id}',this.value)">
              <option value="viewer"${m.role==='viewer'?' selected':''}>viewer</option>
              <option value="admin"${m.role==='admin'?' selected':''}>admin</option>
              <option value="owner"${m.role==='owner'?' selected':''}>owner</option>
            </select>`
          : `<span class="badge ${m.role === 'owner' ? 'completed' : 'pending'}">${m.role}</span>`;
        const removeBtn = isOwner && !isSelf
          ? `<button class="btn btn-xs btn-danger" onclick="removeMember('${m.user_id}')">Remove</button>`
          : '';
        return `<tr>
          <td>${esc(m.email || '—')}</td>
          <td>${roleSelect}</td>
          <td class="cell-time">${relTime(m.created_at)}</td>
          <td>${removeBtn}</td>
        </tr>`;
      }).join('')}</tbody>
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

async function changeMemberRole(userId, role) {
  try {
    await apiFetch(`/projects/${currentProject.id}/members/${userId}`, {
      method: 'PATCH',
      body: JSON.stringify({ role }),
    });
    toast(`Role updated to ${role}`, 'success');
  } catch (e) {
    toast(e.message, 'error');
    loadTeam(); // revert the dropdown on failure
  }
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
  const nameEl = document.getElementById('delete-project-name-display');
  const input = document.getElementById('delete-project-confirm-input');
  const btn = document.getElementById('delete-project-submit-btn');
  if (nameEl) nameEl.textContent = currentProject.name;
  if (input) { input.value = ''; input.placeholder = currentProject.name; }
  if (btn) btn.disabled = true;
  openModal('modal-delete-project');
}

function _checkDeleteConfirm() {
  const input = document.getElementById('delete-project-confirm-input');
  const btn = document.getElementById('delete-project-submit-btn');
  if (!input || !btn || !currentProject) return;
  btn.disabled = input.value.trim() !== currentProject.name;
}

async function _executeDeleteProject() {
  if (!currentProject) return;
  const input = document.getElementById('delete-project-confirm-input');
  if (!input || input.value.trim() !== currentProject.name) return;
  try {
    await apiFetch(`/projects/${currentProject.id}`, { method: 'DELETE' });
    closeModal('modal-delete-project');
    toast('Project deleted', 'success');
    projects = projects.filter(p => p.id !== currentProject.id);
    currentProject = null;
    await loadProjects();
  } catch (e) { toast('Failed to delete: ' + e.message, 'error'); }
}

async function loadSigningSecrets() {
  if (!currentProject) return;
  const el = document.getElementById('signing-secrets-list');
  if (!el) return;
  try {
    const secrets = await apiFetch(`/projects/${currentProject.id}/source-secrets`);
    const entries = Object.entries(secrets);
    if (!entries.length) {
      el.innerHTML = `<div style="font-size:13px;color:var(--text3)">No signing secrets configured.</div>`;
      return;
    }
    el.innerHTML = `<table style="width:100%;font-size:13px;border-collapse:collapse">
      <thead><tr>
        <th style="text-align:left;padding:6px 0;color:var(--text2);font-weight:500;border-bottom:1px solid var(--border)">Provider</th>
        <th style="text-align:left;padding:6px 0;color:var(--text2);font-weight:500;border-bottom:1px solid var(--border)">Secret</th>
        <th style="border-bottom:1px solid var(--border)"></th>
      </tr></thead>
      <tbody>${entries.map(([provider, info]) => `
        <tr>
          <td style="padding:8px 0;font-family:var(--mono);font-weight:500">${esc(provider)}</td>
          <td style="padding:8px 0;font-family:var(--mono);color:var(--text3)">${esc(info.preview)}</td>
          <td style="padding:8px 0;text-align:right">
            <button class="btn btn-xs btn-danger" onclick="deleteSigningSecret('${esc(provider)}')">Remove</button>
          </td>
        </tr>
      `).join('')}</tbody>
    </table>`;
  } catch (e) {
    el.innerHTML = `<div style="font-size:13px;color:var(--danger)">${esc(e.message)}</div>`;
  }
}

async function saveSigningSecret() {
  if (!currentProject) return;
  const provider = document.getElementById('signing-provider').value;
  const secret = document.getElementById('signing-secret').value.trim();
  if (!secret) { toast('Enter a secret value', 'error'); return; }
  try {
    await apiFetch(`/projects/${currentProject.id}/source-secrets/${provider}`, {
      method: 'PUT',
      body: JSON.stringify({ secret }),
    });
    document.getElementById('signing-secret').value = '';
    toast(`${provider} secret saved`, 'success');
    loadSigningSecrets();
  } catch (e) { toast(e.message, 'error'); }
}

async function deleteSigningSecret(provider) {
  if (!currentProject) return;
  if (!confirm(`Remove ${provider} signing secret?`)) return;
  try {
    await apiFetch(`/projects/${currentProject.id}/source-secrets/${provider}`, { method: 'DELETE' });
    toast(`${provider} secret removed`, 'success');
    loadSigningSecrets();
  } catch (e) { toast(e.message, 'error'); }
}

// ---------------------------------------------------------------------------
// Modal helpers
// ---------------------------------------------------------------------------
function openModal(id) { document.getElementById(id).classList.add('open'); }
function closeModal(id) {
  document.getElementById(id).classList.remove('open');
  if (id === 'modal-dest') _editingDestId = null;
}

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

function _failureCategoryAdvice(cat) {
  const advice = {
    TIMEOUT:        'Destination took too long to respond. Check if the endpoint is under load or increase the timeout window.',
    NETWORK:        'Could not reach the destination. Verify the URL is reachable and not behind a firewall.',
    DNS:            'DNS lookup failed. Check the hostname in the destination URL is correct and resolving.',
    SSL:            'SSL/TLS error. Ensure the destination has a valid certificate and is accepting HTTPS connections.',
    SERVER_ERROR:   'Destination returned a 5xx error. The endpoint is running but throwing an internal error — check its logs.',
    CLIENT_ERROR:   'Destination returned a 4xx error. The request format may be wrong — check your transform config or payload schema.',
    AUTHENTICATION: 'Destination rejected the request as unauthorized. Verify the webhook secret or auth headers are correct.',
    AUTHORIZATION:  'Destination returned 403 Forbidden. The signing credentials may be mismatched.',
    RATE_LIMITING:  'Destination is rate-limiting Relora. Replay will respect Retry-After headers; consider reducing replay rate.',
    CIRCUIT_BREAKER:'Circuit breaker was OPEN at delivery time — destination had too many recent failures. Wait for auto-recovery or reset it manually.',
    TRANSFORM:      'Payload transform failed. Check your JavaScript transform or JSON field map for errors.',
    FILTER:         'Event was filtered out by the destination\'s filter expression and was not delivered.',
    CONFIGURATION:  'Destination is misconfigured. Review the URL, headers, and transform settings.',
  };
  return advice[cat] || '';
}

async function resetCircuitBreaker(destId) {
  try {
    await apiFetch(`/destinations/${destId}/reset-circuit`, { method: 'POST' });
    toast('Circuit breaker reset — destination set to CLOSED', 'success');
    await loadDestinations();
  } catch (e) { toast(e.message, 'error'); }
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

  // Eagerly clean up orphaned incidents so the page never shows stale OPEN entries
  if (_dlqDepth === 0) {
    apiFetch('/dlq/resolve-all-incidents', { method: 'POST' }).catch(() => {});
  }

  let hasError = false;
  let errorMessages = [];

  // Load health data
  try {
    const healthData = await apiFetch('/dlq/health');
    updateHealthScore(healthData);
  } catch (error) {
    console.error('Error loading health data:', error);
    errorMessages.push('Health data unavailable');
    hasError = true;
  }

  // Load incident summary
  try {
    const incidentsData = await apiFetch('/dlq/incidents?limit=100');
    updateIncidentSummary(incidentsData);
    updateActiveIncidents(incidentsData);
  } catch (error) {
    console.error('Error loading incidents:', error);
    errorMessages.push('Incidents unavailable');
    hasError = true;
  }

  // Load classifications
  try {
    const classificationsData = await apiFetch('/dlq/classifications');
    updateFailureBreakdown(classificationsData);
  } catch (error) {
    console.error('Error loading classifications:', error);
    errorMessages.push('Classifications unavailable');
    hasError = true;
  }

  // Load trends
  try {
    const trendsData = await apiFetch('/dlq/trends');
    updateTrendGraph(trendsData);
  } catch (error) {
    console.error('Error loading trends:', error);
    errorMessages.push('Trends unavailable');
    hasError = true;
  }

  // Load root causes
  try {
    const rootCausesData = await apiFetch('/dlq/root-causes');
    updateRootCauses(rootCausesData);
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

  valueEl.textContent  = status === 'HEALTHY' ? 100 : score;
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
  
  const active = incidents.filter(i => i.state === 'OPEN' || i.state === 'INVESTIGATING');
  totalEl.textContent = active.length;
  openEl.textContent = active.length;
  criticalEl.textContent = active.filter(i => i.severity === 'critical').length;

  const uniqueDestinations = new Set(active.map(i => i.destination_id).filter(Boolean));
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
    container.innerHTML = '<div class="dlq-inv-empty">No active incidents — system is healthy</div>';
    return;
  }

  container.innerHTML = openIncidents.slice(0, 10).map(incident => {
    const isCritical = incident.severity === 'critical';
    const timeAgo = getTimeAgo(incident.last_seen_at);
    const destId = incident.destination_id || '';
    const destName = esc(incident.destination_name || incident.failure_category || 'destination');

    return `
      <div class="incident-item ${isCritical ? 'critical' : 'warning'}" data-incident-id="${incident.id}">
        <div class="incident-header">
          <span class="incident-title">${esc(incident.root_cause || incident.failure_category || 'Unknown')}</span>
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
            <strong>Recommendation:</strong> ${esc(incident.recommended_action)}
          </div>
        ` : ''}
        <div class="incident-actions">
          ${destId ? `<button class="btn btn-xs btn-primary" onclick="replayDLQForDestination('${destId}', '${destName}')">Replay →</button>` : ''}
          <button class="btn btn-xs btn-secondary" onclick="resolveIncident('${incident.id}')">Mark resolved</button>
        </div>
      </div>
    `;
  }).join('');
}

async function resolveIncident(incidentId) {
  // Optimistic removal so the card disappears immediately
  const card = document.querySelector(`[data-incident-id="${incidentId}"]`);
  if (card) card.remove();
  try {
    await apiFetch(`/dlq/incidents/${incidentId}/resolve`, { method: 'PATCH' });
    toast('Incident marked as resolved', 'success');
    loadDLQIntelligence();
  } catch (e) {
    toast(e.message || 'Failed to resolve incident', 'error');
    loadDLQIntelligence(); // restore the card if the call failed
  }
}

async function clearAllFailedEvents() {
  if (!confirm('Clear all failed delivery records? This permanently removes them from the queue and cannot be undone.')) return;
  try {
    const res = await apiFetch('/dlq/archive?older_than_days=0', { method: 'DELETE' });
    toast(`Cleared ${res.archived ?? 0} failed event${res.archived !== 1 ? 's' : ''}`, 'success');
    await _pollDLQState();
    loadDLQIntelligence();
  } catch (e) { toast(e.message || 'Failed to clear events', 'error'); }
}

async function replayDLQForDestination(destId, destName) {
  if (!confirm(`Replay all failed webhooks for "${destName}" from the last 7 days?\n\nThis will re-queue them for delivery at 60 events/min.`)) return;
  const now = new Date();
  const from = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
  try {
    await apiFetch('/webhooks/replay-window', {
      method: 'POST',
      body: JSON.stringify({
        from_time: from.toISOString(),
        to_time: now.toISOString(),
        destination_id: destId,
        replay_rate_per_minute: 60,
      }),
    });
    toast(`Replay queued for ${destName}`, 'success');
    loadDLQIntelligence();
  } catch (e) { toast(e.message || 'Replay failed', 'error'); }
}

async function loadTopDestinations() {
  try {
    const container = document.getElementById('top-destinations');
    const healthPromises = destinations.slice(0, 5).map(async (dest) => {
      try {
        return await apiFetch(`/destinations/${dest.id}/health`);
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

// ---------------------------------------------------------------------------
// Recovery Center
// ---------------------------------------------------------------------------
let _recoveryTab = 'failures';

async function loadRecovery() {
  try {
    const d = await apiFetch('/dashboard');
    _renderRecoveryFailures(d.recent_failures || []);
    _renderRecoveryIncidents(d.active_incidents || []);
    _renderRecoveryDLQ(d.kpis || {});
    _renderRecoveryReplays();
  } catch (e) {
    const el = document.getElementById('recovery-failures-body');
    if (el) el.innerHTML = '<div style="padding:24px;color:var(--danger);font-size:13px">Failed to load recovery data</div>';
  }
}

function switchRecoveryTab(tab) {
  _recoveryTab = tab;
  document.querySelectorAll('[data-rtab]').forEach(t => t.classList.toggle('active', t.dataset.rtab === tab));
  document.querySelectorAll('.recovery-tab').forEach(t => { t.style.display = 'none'; });
  const el = document.getElementById('recovery-' + tab);
  if (el) el.style.display = '';
  if (tab === 'alert-settings') loadAlerts();
}

function _renderRecoveryFailures(failures) {
  const el = document.getElementById('recovery-failures-body');
  if (!el) return;
  if (!failures.length) {
    el.innerHTML = '<div class="empty-state"><svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.25"><circle cx="12" cy="12" r="10"/><path d="M8 12h8"/></svg><div class="title">No recent failures</div><div class="desc">System is delivering cleanly</div></div>';
    return;
  }
  const catColor = { TIMEOUT: 'var(--warn)', HTTP_ERROR: 'var(--danger)', CONNECTION_ERROR: 'var(--danger)', UNKNOWN: 'var(--text3)' };
  const rows = failures.map(f => '<tr><td style="font-size:12px;color:var(--text3);font-family:var(--mono)">' + relTime(f.last_seen_at || f.created_at) + '</td><td style="font-size:13px">' + esc(f.destination_name || '—') + '</td><td><span style="font-family:var(--mono);font-size:11px;color:' + (catColor[f.category] || 'var(--text3)') + '">' + esc(f.category || 'UNKNOWN') + '</span></td><td><button class="btn btn-xs btn-secondary" onclick="navTo(\'dlq-intelligence\')">Investigate</button></td></tr>').join('');
  el.innerHTML = '<table><thead><tr><th>Time</th><th>Destination</th><th>Category</th><th style="width:100px"></th></tr></thead><tbody>' + rows + '</tbody></table>';
}

function _renderRecoveryIncidents(incidents) {
  const el = document.getElementById('recovery-incidents-body');
  if (!el) return;
  if (!incidents.length) {
    el.innerHTML = '<div class="empty-state"><svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.25"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/></svg><div class="title">No active incidents</div><div class="desc">System is healthy</div></div>';
    return;
  }
  const sevColor = { critical: 'var(--danger)', high: 'var(--warn)', medium: 'var(--info)', low: 'var(--text3)' };
  const rows = incidents.map(i => '<tr><td><span style="font-family:var(--mono);font-size:10px;color:' + (sevColor[i.severity] || 'var(--text3)') + '">' + esc((i.severity || 'low').toUpperCase()) + '</span></td><td style="font-size:12px">' + esc(i.category || '—') + (i.subcategory ? ' · ' + esc(i.subcategory) : '') + '</td><td style="font-size:12px;color:var(--text2)">' + esc(i.destination_name || '—') + '</td><td style="font-family:var(--mono);font-size:12px">' + (i.affected_count || 0) + '</td><td><span class="badge ' + (i.state === 'OPEN' ? 'failed' : 'pending') + '">' + esc(i.state || '—') + '</span></td><td><button class="btn btn-xs btn-secondary" onclick="navTo(\'dlq-intelligence\')">View</button></td></tr>').join('');
  el.innerHTML = '<table><thead><tr><th>Severity</th><th>Category</th><th>Destination</th><th>Affected</th><th>State</th><th style="width:56px"></th></tr></thead><tbody>' + rows + '</tbody></table>';
}

function _renderRecoveryDLQ(kpis) {
  const el = document.getElementById('recovery-dlq-body');
  if (!el) return;
  const dlq = kpis.dlq_depth || 0;
  if (dlq === 0) {
    el.innerHTML = '<div class="empty-state"><svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.25"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.12"/></svg><div class="title">DLQ is empty</div><div class="desc">All events delivered successfully</div></div>';
    return;
  }
  el.innerHTML = '<div style="padding:20px;border:1px solid rgba(224,82,82,.2);background:rgba(224,82,82,.04);margin-bottom:16px"><div style="display:flex;align-items:center;gap:16px"><div style="font-family:var(--mono);font-size:32px;font-weight:500;color:var(--danger)">' + dlq + '</div><div><div style="font-weight:600;color:var(--text);margin-bottom:4px">Events in Dead Letter Queue</div><div style="font-size:12px;color:var(--text3)">These events failed all retry attempts and require manual intervention.</div></div><button class="btn btn-primary" style="margin-left:auto" onclick="navTo(\'replay\')">Replay all →</button></div></div><div style="font-size:12px;color:var(--text3);padding:8px 0">Use Bulk Replay to re-attempt delivery, or navigate to DLQ Intelligence for root cause analysis.</div>';
}

async function _renderRecoveryReplays() {
  const el = document.getElementById('recovery-replays-body');
  if (!el) return;
  try {
    const data = await apiFetch('/replay-jobs?limit=20');
    const jobs = Array.isArray(data) ? data : [];
    if (!jobs.length) {
      el.innerHTML = '<div class="empty-state"><svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.25"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.12"/></svg><div class="title">No replay history</div><div class="desc">Bulk replay jobs appear here after execution</div></div>';
      return;
    }
    const rows = jobs.map(j => {
      const badgeCls = j.status === 'completed' ? 'completed' : j.status === 'running' ? 'processing' : 'pending';
      const failed = j.failed_count ?? 0;
      return '<tr>'
        + '<td style="font-size:12px;color:var(--text3);font-family:var(--mono)">' + relTime(j.created_at) + '</td>'
        + '<td><span class="badge ' + badgeCls + '">' + esc(j.status || '—') + '</span></td>'
        + '<td style="font-family:var(--mono);font-size:12px">' + (j.total_count != null ? j.total_count : '—') + '</td>'
        + '<td style="font-family:var(--mono);font-size:12px;color:var(--success)">' + (j.processed_count != null ? j.processed_count : '—') + '</td>'
        + '<td style="font-family:var(--mono);font-size:12px;color:' + (failed > 0 ? 'var(--danger)' : 'var(--text3)') + '">' + (j.failed_count != null ? failed : '—') + '</td>'
        + '</tr>';
    }).join('');
    el.innerHTML = '<table><thead><tr><th>Started</th><th>Status</th><th>Total</th><th>Delivered</th><th>Failed</th></tr></thead><tbody>' + rows + '</tbody></table>';
  } catch (e) {
    el.innerHTML = '<div style="padding:24px;text-align:center;color:var(--text3);font-size:13px">Replay history unavailable</div>';
  }
}

// ---------------------------------------------------------------------------
// Pipeline page
// ---------------------------------------------------------------------------
let _pipeDestCache = {};
let _pipeActiveDest = null;

async function loadPipeline() {
  _pipeDestCache = {};
  _pipeActiveDest = null;
  try {
    const [d, sys] = await Promise.all([
      apiFetch('/dashboard'),
      fetch('/health/detailed', { credentials: 'include' }).then(r => r.ok ? r.json() : {}),
    ]);
    const kpis   = d.kpis || {};
    const worker = sys.checks?.worker || {};
    const queue  = sys.checks?.queue  || {};

    function set(id, v, color) {
      const e = document.getElementById(id);
      if (!e) return;
      e.textContent = v;
      e.style.color = color || '';
    }

    const qDepth  = queue.depth ?? 0;
    const dlqDepth = kpis.dlq_depth ?? 0;

    set('pipe-receive',      kpis.total_24h     != null ? kpis.total_24h.toLocaleString()     : '—');
    set('pipe-persist',      kpis.total_24h     != null ? kpis.total_24h.toLocaleString()     : '—');
    set('pipe-relay',        kpis.delivered_24h != null ? kpis.delivered_24h.toLocaleString() : '—');
    set('pipe-retry',        qDepth.toLocaleString(),   qDepth > 0   ? 'var(--warn)'   : '');
    set('pipe-dlq',          dlqDepth.toLocaleString(), dlqDepth > 0 ? 'var(--danger)' : '');
    set('pipe-replay-count', '—');

    // Ingest card
    const workerOk = worker.status === 'ok';
    set('pipe-worker-status', workerOk ? 'Online' : 'Stalled', workerOk ? 'var(--success)' : 'var(--danger)');
    set('pipe-queue-depth',   qDepth.toLocaleString());
    const rate = kpis.success_rate_24h;
    const rateColor = rate >= 99 ? 'var(--success)' : rate >= 95 ? 'var(--warn)' : 'var(--danger)';
    set('pipe-success-rate',  rate != null ? rate.toFixed(1) + '%' : '—', rate != null ? rateColor : '');
    set('pipe-p95',           kpis.p95_latency_ms ? kpis.p95_latency_ms + ' ms' : '—');

    // Failure pressure card
    const cb = kpis.circuit_breakers ? (kpis.circuit_breakers.open || 0) + (kpis.circuit_breakers.half_open || 0) : 0;
    set('pipe-dlq-detail',   dlqDepth.toLocaleString(),                      dlqDepth > 0 ? 'var(--danger)' : '');
    set('pipe-cb-detail',    cb.toLocaleString(),                             cb > 0        ? 'var(--danger)' : '');
    set('pipe-inc-detail',   (d.active_incidents || []).filter(i => i.state === 'OPEN').length.toLocaleString());
    set('pipe-stuck-detail', (worker.stuck_jobs || 0).toLocaleString(),      worker.stuck_jobs > 0 ? 'var(--warn)' : '');

    // Destinations mini-list in stats card
    const destEl = document.getElementById('pipe-dest-list');
    if (destEl && destinations.length) {
      destEl.innerHTML = destinations.slice(0, 8).map(dest => {
        const color = dest.circuit_state === 'closed' ? 'var(--success)' : 'var(--danger)';
        return `<div class="kv-row">
          <span class="kv-key" style="max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(dest.name || dest.url)}</span>
          <span class="kv-val" style="color:${color};font-family:var(--mono);font-size:11px">${(dest.circuit_state || '—').toUpperCase()}</span>
        </div>`;
      }).join('');
    }

    // Destination performance section
    const destSection = document.getElementById('pipe-dest-section');
    if (destSection) {
      if (destinations.length > 0) {
        destSection.style.display = '';
        _renderPipeDestTabs();
      } else {
        destSection.style.display = 'none';
      }
    }
  } catch (e) {}
}

function _renderPipeDestTabs() {
  const tabs = document.getElementById('pipe-dest-tabs');
  if (!tabs) return;
  tabs.innerHTML = destinations.map((d, i) =>
    `<div class="tab${i === 0 ? ' active' : ''}" onclick="switchPipeDest('${d.id}',this)">${esc(d.name || _shortUrl(d.url))}</div>`
  ).join('');
  if (destinations.length > 0) {
    switchPipeDest(destinations[0].id, tabs.querySelector('.tab'));
  }
}

async function switchPipeDest(id, tabEl) {
  if (tabEl) {
    document.querySelectorAll('#pipe-dest-tabs .tab').forEach(t => t.classList.toggle('active', t === tabEl));
  }
  _pipeActiveDest = id;
  const content = document.getElementById('pipe-dest-content');
  if (!content) return;

  content.innerHTML = '<div style="padding:32px;text-align:center;color:var(--text3);font-size:13px">Loading…</div>';
  const dest = destinations.find(d => d.id === id);

  const [statsResult, webhooksResult] = await Promise.allSettled([
    apiFetch(`/destinations/${id}/stats`),
    apiFetch(`/webhooks?destination_id=${id}&limit=28&page=1`),
  ]);

  if (_pipeActiveDest !== id) return;

  const stats    = statsResult.status    === 'fulfilled' ? statsResult.value              : null;
  const webhooks = webhooksResult.status === 'fulfilled' ? (webhooksResult.value?.webhooks || []) : [];

  _renderPipeDestView(stats, dest, webhooks);
}

function _buildDeliveryTrail(webhooks) {
  if (!webhooks || !webhooks.length) {
    return '<span class="pipe-dest-trail-empty">No deliveries recorded yet</span>';
  }
  return webhooks.map(w => {
    const cls   = w.status === 'completed' ? 'ok' : w.status === 'failed' ? 'fail' : 'pend';
    const label = `${w.status} · ${relTime(w.updated_at || w.created_at)}`;
    return `<div class="pipe-dest-trail-dot pipe-dest-trail-dot--${cls}" title="${esc(label)}"></div>`;
  }).join('');
}

function _renderPipeDestView(stats, dest, webhooks) {
  const content = document.getElementById('pipe-dest-content');
  if (!content || !dest) return;

  const rate    = stats ? (stats.success_rate ?? stats.delivery_rate ?? null) : null;
  const pct     = rate != null ? Math.round(rate * 100) / 100 : null;
  const rateCls = pct == null ? '' : pct >= 99 ? 'pipe-dest-rate--good' : pct >= 95 ? 'pipe-dest-rate--warn' : 'pipe-dest-rate--bad';
  const rateStr = pct != null ? pct.toFixed(1) + '%' : '—';
  const barW    = pct != null ? Math.min(pct, 100) : 0;
  const barCol  = pct >= 99 ? '#00D47E' : pct >= 95 ? '#E8A838' : '#E05252';

  const circuit      = dest.circuit_state || 'closed';
  const circuitLabel = circuit === 'closed' ? 'Circuit closed' : circuit === 'half_open' ? 'Half open — recovering' : 'Circuit open — suspended';
  const circuitColor = circuit === 'closed' ? '#00D47E' : circuit === 'half_open' ? '#E8A838' : '#E05252';

  const delivered = stats?.delivered_24h ?? stats?.total_delivered ?? null;
  const failed    = stats?.failed_24h    ?? stats?.total_failed    ?? null;

  const trailHtml = _buildDeliveryTrail(webhooks);

  content.innerHTML = `
    <div class="pipe-dest-card">

      <!-- Delivery trail — most recent at left -->
      <div class="pipe-dest-trail-hdr">Last ${webhooks.length || 0} deliveries</div>
      <div class="pipe-dest-trail">${trailHtml}</div>

      <!-- Rate + metrics -->
      <div class="pipe-dest-view">
        <div>
          <div class="pipe-dest-rate ${rateCls}">${rateStr}</div>
          <div class="pipe-dest-rate-label">success rate · 7 days</div>
          <div class="pipe-dest-hbar-wrap">
            <div class="pipe-dest-hbar" style="width:${barW}%;background:${barCol}"></div>
          </div>
          <div class="pipe-dest-circuit">
            <div class="pipe-dest-circuit-dot" style="background:${circuitColor}"></div>
            <span>${esc(circuitLabel)}</span>
          </div>
          ${dest.is_enabled === false ? '<div style="margin-top:6px;font-size:10px;color:var(--text3)">Destination disabled</div>' : ''}
        </div>

        <div class="kv-list" style="border:none;padding:0">
          ${delivered != null ? `<div class="kv-row"><span class="kv-key">Delivered 24h</span><span class="kv-val" style="color:var(--success)">${Number(delivered).toLocaleString()}</span></div>` : ''}
          ${failed    != null ? `<div class="kv-row"><span class="kv-key">Failed 24h</span><span class="kv-val"${failed > 0 ? ' style="color:var(--danger)"' : ''}>${Number(failed).toLocaleString()}</span></div>` : ''}
          ${stats?.p95_latency_ms  != null ? `<div class="kv-row"><span class="kv-key">P95 latency</span><span class="kv-val">${stats.p95_latency_ms} ms</span></div>` : ''}
          ${stats?.retry_count_24h != null ? `<div class="kv-row"><span class="kv-key">Retries 24h</span><span class="kv-val">${Number(stats.retry_count_24h).toLocaleString()}</span></div>` : ''}
          <div class="kv-row"><span class="kv-key">Max retries</span><span class="kv-val">${dest.max_retries ?? '—'}</span></div>
          <div class="kv-row">
            <span class="kv-key">Endpoint</span>
            <span class="kv-val" style="font-size:11px;font-family:var(--mono);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:220px" title="${esc(dest.url)}">${esc(_shortUrl(dest.url))}</span>
          </div>
        </div>
      </div>

      <div style="margin-top:16px;padding-top:14px;border-top:1px solid #1C2A3A;display:flex;gap:8px">
        <button class="btn btn-secondary btn-sm" onclick="viewDestSla('${dest.id}','${esc(dest.name || '')}')">View SLA →</button>
        <button class="btn btn-secondary btn-sm" onclick="navTo('analytics')">Analytics →</button>
        <button class="btn btn-secondary btn-sm" onclick="navTo('destinations')">Manage →</button>
      </div>
    </div>`;
}

// ---------------------------------------------------------------------------
// Timeline
// ---------------------------------------------------------------------------
var _tlFilter = 'all';
var _tlEntries = [];
var _tlOffset = 0;
var TL_LIMIT = 50;

var _tlTypeMap = {
  destination: 'config', alert_config: 'config', project: 'config',
  api_key: 'config', team_member: 'config', settings: 'config',
  webhook: 'delivery', replay_job: 'replay'
};

var _tlIcon = {
  delivery: '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M22 2L11 13"/><path d="M22 2L15 22l-4-9-9-4 20-7z"/></svg>',
  incident: '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/></svg>',
  config:   '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="3"/></svg>',
  replay:   '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.12"/></svg>'
};

var _tlColor = { delivery: 'var(--success)', incident: 'var(--danger)', config: 'var(--text3)', replay: 'var(--info)' };

async function loadTimeline(offset) {
  if (offset == null) offset = 0;
  _tlOffset = offset;
  const body = document.getElementById('timeline-body');
  const pag  = document.getElementById('timeline-pagination');
  if (!body) return;
  body.innerHTML = '<div style="padding:40px;text-align:center;color:var(--text3);font-size:13px">Loading…</div>';
  try {
    const data = await apiFetch('/audit-log?limit=' + TL_LIMIT + '&offset=' + offset);
    _tlEntries = (data.entries || []).map(function(e) {
      return Object.assign({}, e, { _tltype: _tlTypeMap[e.resource_type] || 'config' });
    });
    _renderTimeline();
    if (pag) {
      const hasPrev = offset > 0;
      const hasNext = _tlEntries.length === TL_LIMIT;
      pag.innerHTML = (hasPrev || hasNext) ? '<button class="btn btn-secondary btn-sm" onclick="loadTimeline(' + (offset - TL_LIMIT) + ')" ' + (hasPrev ? '' : 'disabled') + '>← Prev</button><span style="font-size:12px;color:var(--text3);padding:0 12px">' + (offset + 1) + '–' + (offset + _tlEntries.length) + '</span><button class="btn btn-secondary btn-sm" onclick="loadTimeline(' + (offset + TL_LIMIT) + ')" ' + (hasNext ? '' : 'disabled') + '>Next →</button>' : '';
    }
  } catch (e) {
    body.innerHTML = '<div style="padding:40px;text-align:center;color:var(--text3);font-size:13px">Timeline unavailable</div>';
  }
}

function filterTimeline(btn, filter) {
  _tlFilter = filter;
  document.querySelectorAll('.tl-filter').forEach(function(b) { b.classList.toggle('active', b === btn); });
  _renderTimeline();
}

function _renderTimeline() {
  const body = document.getElementById('timeline-body');
  if (!body) return;
  const entries = _tlFilter === 'all' ? _tlEntries : _tlEntries.filter(function(e) { return e._tltype === _tlFilter; });
  if (!entries.length) {
    body.innerHTML = '<div class="empty-state"><svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.25"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg><div class="title">No entries</div><div class="desc">No ' + (_tlFilter === 'all' ? '' : _tlFilter + ' ') + 'events recorded yet</div></div>';
    return;
  }
  var lastDate = '';
  var resourceLabel = { destination: 'Destination', alert_config: 'Alert', webhook: 'Webhook', project: 'Project', replay_job: 'Replay', api_key: 'API Key', team_member: 'Team Member' };
  body.innerHTML = entries.map(function(e) {
    const d = new Date(e.created_at);
    const dateStr = d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
    const timeStr = d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    var dateSep = '';
    if (dateStr !== lastDate) { dateSep = '<div class="tl-date-sep">' + dateStr + '</div>'; lastDate = dateStr; }
    const type  = e._tltype;
    const color = _tlColor[type] || 'var(--text3)';
    const icon  = _tlIcon[type] || _tlIcon.config;
    const changes = e.changes || {};
    const detail = (changes.after && changes.after.name) || (changes.before && changes.before.name) || (changes.after && changes.after.url && changes.after.url.substring(0, 40)) || (e.resource_id ? e.resource_id.substring(0, 20) + '…' : '');
    return dateSep + '<div class="tl-row" data-tltype="' + esc(e._tltype) + '"><div class="tl-time">' + timeStr + '</div><div class="tl-dot" style="background:' + color + '">' + icon + '</div><div class="tl-content"><span class="tl-action" style="color:' + color + '">' + esc(e.action || '—') + '</span><span class="tl-resource">' + esc(resourceLabel[e.resource_type] || e.resource_type || '—') + '</span>' + (detail ? '<span class="tl-detail">' + esc(detail) + '</span>' : '') + '</div>' + (e.ip_address ? '<div class="tl-ip">' + esc(e.ip_address) + '</div>' : '') + '</div>';
  }).join('');
}

// ---------------------------------------------------------------------------
// Insights — Weekly Reliability Briefing
// ---------------------------------------------------------------------------
let _insightReport = null;
let _insightMessages = [];

function switchInsightTab(tab) {
  ['this-week', 'trends', 'archive'].forEach(t => {
    document.getElementById('ins-tab-' + t).classList.toggle('active', t === tab);
    document.getElementById('ins-panel-' + t).style.display = t === tab ? '' : 'none';
  });
  document.getElementById('ins-generate-btn').style.display = tab === 'this-week' ? '' : 'none';
  if (tab === 'archive') _loadInsightArchive();
  if (tab === 'trends') loadAnalytics();
}

async function loadInsights() {
  switchInsightTab('this-week');
  _showInsLoading(true);
  try {
    const r = await apiFetch('/insights/reports/current');
    _insightReport = r;
    _insightMessages = [];
    _renderInsightReport(r);
  } catch (e) {
    toast('Failed to load weekly report: ' + e.message, 'error');
    _showInsLoading(false);
  }
}

async function insightForceGenerate() {
  const btn = document.getElementById('ins-generate-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Generating…'; }
  _showInsLoading(true);
  try {
    const r = await apiFetch('/insights/generate', { method: 'POST' });
    _insightReport = r;
    _insightMessages = [];
    _renderInsightReport(r);
    toast('Report regenerated', 'success');
  } catch (e) {
    toast('Failed to regenerate: ' + e.message, 'error');
    _showInsLoading(false);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.12"/></svg> Regenerate';
    }
  }
}

function _showInsLoading(on) {
  const loading = document.getElementById('ins-loading');
  const report  = document.getElementById('ins-report');
  if (loading) loading.style.display = on ? '' : 'none';
  if (report)  report.style.display  = on ? 'none' : '';
}

function _insGradeColor(grade) {
  if (!grade) return 'neutral';
  const g = grade[0];
  if (g === 'A') return 'success';
  if (g === 'B') return 'info';
  if (g === 'C') return 'warn';
  return 'danger';
}

function _buildRuleBasedBriefing(r) {
  const data       = r.report_data || {};
  const overview   = data.overview || {};
  const replay     = data.replay   || {};
  const incidents  = data.incidents || {};
  const whatChanged = data.what_changed || [];

  const score    = r.reliability_score ?? 100;
  const grade    = r.grade || '—';
  const delta    = r.score_delta;
  const total    = overview.total_deliveries  || 0;
  const failed   = overview.failed_deliveries || 0;
  const recovered = replay.events_recovered   || 0;

  const parts = [];

  let lead = `${score.toFixed(1)}% reliability this week (Grade ${grade})`;
  if (delta !== null && delta !== undefined) {
    const dir = delta >= 0 ? 'up' : 'down';
    lead += `, ${Math.abs(delta).toFixed(1)}% ${dir} from last week`;
  }
  parts.push(lead + '.');

  if (total > 0) {
    if (failed > 0) {
      parts.push(`${total.toLocaleString()} deliveries attempted — ${failed.toLocaleString()} failed.`);
    } else {
      parts.push(`${total.toLocaleString()} deliveries completed with no failures.`);
    }
  }

  const incOpened = incidents.opened || 0;
  if (incOpened > 0) {
    parts.push(`${incOpened} incident${incOpened > 1 ? 's' : ''} opened this week.`);
  }

  if (recovered > 0) {
    parts.push(`${recovered.toLocaleString()} failed deliveries recovered via replay.`);
  }

  const topItem = whatChanged.find(i => i.type !== 'stable' && i.explanation);
  if (topItem) parts.push(topItem.explanation);

  return parts.join(' ');
}

function _renderInsightReport(r) {
  _showInsLoading(false);
  const data         = r.report_data  || {};
  const overview     = data.overview  || {};
  const streaks      = data.streaks   || {};
  const replay       = data.replay    || {};
  const destinations = data.destinations || {};
  const whatChanged  = data.what_changed || [];
  const recs         = data.recommendations || [];
  const incidents    = data.incidents || {};

  const ws      = new Date(r.week_start);
  const we      = new Date(r.week_end);
  const weekEnd = new Date(we.getTime() - 86400000);
  const weekLabel = ws.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ' – ' +
    weekEnd.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });

  const grade = r.grade || '—';
  const score = r.reliability_score ?? 100;
  const delta = r.score_delta;

  const deltaHtml = (delta === null || delta === undefined) ? '' :
    `<span class="ins-cover-delta ins-delta-${delta >= 0 ? 'up' : 'down'}">${delta >= 0 ? '▲' : '▼'} ${Math.abs(delta).toFixed(1)}%</span>`;

  const total    = (overview.total_deliveries  || 0).toLocaleString();
  const failed   = (overview.failed_deliveries || 0).toLocaleString();
  const incCount = incidents.opened || 0;
  const p95      = overview.p95_latency_ms ? overview.p95_latency_ms + 'ms' : '—';

  const briefingText = r.ai_summary || _buildRuleBasedBriefing(r);
  const aiHtml = `<div class="ins-briefing-text">${esc(briefingText)}</div>`;

  const topSuccesses  = destinations.top_successes  || [];
  const biggestIssues = destinations.biggest_issues || [];
  const destHtml = (topSuccesses.length || biggestIssues.length) ? `
    <div class="ins-section">
      <div class="ins-section-hdr">Destination Highlights</div>
      <div class="ins-dest-grid">
        <div class="ins-dest-col">
          <div class="ins-dest-col-lbl">Top Performers</div>
          ${topSuccesses.length ? topSuccesses.map(d => _insDestRow(d, 'success')).join('') : '<div class="ins-empty-inline">No active destinations.</div>'}
        </div>
        <div class="ins-dest-col">
          <div class="ins-dest-col-lbl">Needs Attention</div>
          ${biggestIssues.length ? biggestIssues.map(d => _insDestRow(d, 'issue')).join('') : '<div class="ins-empty-inline">No issues this week.</div>'}
        </div>
      </div>
    </div>` : '';

  document.getElementById('ins-report').innerHTML = `
    <div class="ins-cover">
      <div class="ins-cover-left">
        <div class="ins-cover-week">${esc(weekLabel)}</div>
        <div class="ins-cover-title">Weekly Reliability Briefing</div>
        <div class="ins-cover-score-row">
          <span class="ins-cover-score">${score.toFixed(1)}%</span>
          ${deltaHtml}
        </div>
      </div>
      <div class="ins-cover-right">
        <div class="ins-grade-badge ins-grade-${_insGradeColor(grade)}">${esc(grade)}</div>
      </div>
    </div>

    <div class="ins-briefing">
      <div class="ins-briefing-eyebrow">Executive Briefing</div>
      ${aiHtml}
    </div>

    <div class="ins-stats-row">
      <span class="ins-stat"><strong>${total}</strong> deliveries</span>
      <span class="ins-stat-sep">·</span>
      <span class="ins-stat"${overview.failed_deliveries ? ' style="color:var(--danger)"' : ''}><strong>${failed}</strong> failures</span>
      <span class="ins-stat-sep">·</span>
      <span class="ins-stat"><strong>${incCount}</strong> incident${incCount === 1 ? '' : 's'}</span>
      <span class="ins-stat-sep">·</span>
      <span class="ins-stat">p95 <strong>${p95}</strong></span>
    </div>

    <div class="ins-section">
      <div class="ins-section-hdr">What Changed</div>
      ${_buildChangesHtml(whatChanged)}
    </div>

    ${_buildStreaksHtml(streaks)}
    ${_buildReplayHtml(replay, score)}
    ${destHtml}
    ${_buildRecsHtml(recs)}

    ${r.ai_summary ? `<div class="ins-section ins-qa-section">
      <div class="ins-section-hdr">Ask About This Week</div>
      <div class="ins-qa-chips">
        ${['Why did my grade change?', 'What caused reliability issues?', 'Which destination needs attention?', 'What improved most?', 'What should I prioritize next week?']
          .map(q => `<button class="ins-qa-chip" onclick="askInsightPreset(${JSON.stringify(q)})">${esc(q)}</button>`)
          .join('')}
      </div>
      <div class="ins-chat-messages" id="ins-chat-messages"></div>
      <div class="ins-chat-input-row">
        <input class="ins-chat-input" id="ins-chat-input" type="text" placeholder="Ask a question about this report…" onkeydown="if(event.key==='Enter')sendInsightQuestion()">
        <button class="btn btn-primary btn-sm" onclick="sendInsightQuestion()">Ask</button>
      </div>
    </div>` : ''}
  `;
}

function _buildChangesHtml(whatChanged) {
  const typeLabel = {
    reliability_shift: 'RELIABILITY',
    latency_shift:     'LATENCY',
    incident:          'INCIDENT',
    resolved:          'RESOLVED',
    replay:            'REPLAY',
    schema_drift:      'SCHEMA',
    new_destination:   'DESTINATION',
    stable:            'STABLE',
  };
  if (!whatChanged.length) return '<div class="ins-empty-inline">No significant changes this week.</div>';
  return whatChanged.map(item => {
    const badge  = typeLabel[item.type] || item.type.toUpperCase().replace(/_/g, ' ');
    const impact = item.impact || 'neutral';
    return `<div class="ins-change-item ins-change-${impact}">
      <div class="ins-change-head">
        <span class="ins-change-badge ins-badge-${impact}">${badge}</span>
        <span class="ins-change-headline">${esc(item.headline)}</span>
      </div>
      ${item.explanation ? `<div class="ins-change-explanation">${esc(item.explanation)}</div>` : ''}
    </div>`;
  }).join('');
}

function _buildStreaksHtml(streaks) {
  const curDays     = streaks.current_days;
  const longestDays = streaks.longest_days;
  const bestWeek    = streaks.best_week;

  const curVal  = (curDays === null || curDays === undefined) ? 'All time' : curDays + ' days';
  const curSub  = streaks.streak_label || 'No critical incidents on record';
  const longVal = (longestDays !== null && longestDays !== undefined) ? longestDays + ' days' : '—';
  const bestVal = bestWeek ? bestWeek.score.toFixed(1) + '%' : '—';
  const bestSub = bestWeek ? (bestWeek.week_label || '') : 'No archived reports yet';

  return `<div class="ins-section">
    <div class="ins-section-hdr">Reliability Streaks &amp; Records</div>
    <div class="ins-streaks-grid">
      <div class="ins-streak-card">
        <div class="ins-streak-val">${esc(curVal)}</div>
        <div class="ins-streak-lbl">Current Streak</div>
        <div class="ins-streak-sub">${esc(curSub)}</div>
      </div>
      <div class="ins-streak-card">
        <div class="ins-streak-val">${esc(longVal)}</div>
        <div class="ins-streak-lbl">Longest Ever</div>
        <div class="ins-streak-sub">All-time best streak</div>
      </div>
      <div class="ins-streak-card">
        <div class="ins-streak-val">${esc(bestVal)}</div>
        <div class="ins-streak-lbl">Best Week Ever</div>
        <div class="ins-streak-sub">${esc(bestSub)}</div>
      </div>
    </div>
  </div>`;
}

function _buildReplayHtml(replay, currentScore) {
  const recovered = replay.events_recovered || 0;
  if (!recovered) return '';
  const rateWithout = replay.rate_without_replay;
  const savedPts    = (rateWithout !== null && rateWithout !== undefined)
    ? (currentScore - rateWithout).toFixed(1) : null;
  const subText = savedPts !== null
    ? `Without replay, reliability would have been ${replay.rate_without_replay.toFixed(1)}% — <strong>${savedPts} points lower</strong>.`
    : `Replay jobs re-delivered ${recovered.toLocaleString()} events that had previously failed.`;
  return `<div class="ins-section">
    <div class="ins-section-hdr">Replay Impact</div>
    <div class="ins-replay-hero">
      <div class="ins-replay-number">${recovered.toLocaleString()}</div>
      <div class="ins-replay-desc">
        <div class="ins-replay-label">Deliveries Recovered</div>
        <div class="ins-replay-sub">${subText}</div>
      </div>
    </div>
  </div>`;
}

function _insDestRow(d, type) {
  const sr    = (d.success_rate ?? 100).toFixed(1);
  const color = type === 'success' ? 'var(--success)' : 'var(--danger)';
  const total = (d.total || 0).toLocaleString();
  const sub   = (type === 'issue' && d.failed)
    ? d.failed.toLocaleString() + ' failures'
    : total + ' deliveries';
  return `<div class="ins-dest-row">
    <div class="ins-dest-info">
      <div class="ins-dest-name">${esc(d.name)}</div>
      <div class="ins-dest-sub">${sub}</div>
    </div>
    <div class="ins-dest-rate" style="color:${color}">${sr}%</div>
  </div>`;
}

function _buildRecsHtml(recs) {
  if (!recs.length) return '';
  const badge = { high: 'HIGH', medium: 'MEDIUM', low: 'LOW' };
  const cls   = { high: 'rec-high', medium: 'rec-medium', low: 'rec-low' };
  return `<div class="ins-section">
    <div class="ins-section-hdr">Recommendations</div>
    ${recs.map(r => `<div class="ins-rec">
      <div class="ins-rec-head">
        <span class="ins-rec-badge ${cls[r.priority] || ''}">${badge[r.priority] || r.priority.toUpperCase()}</span>
        <span class="ins-rec-title">${esc(r.title)}</span>
      </div>
      <div class="ins-rec-body">${esc(r.body)}</div>
    </div>`).join('')}
  </div>`;
}

async function _loadInsightArchive() {
  const el = document.getElementById('ins-archive-list');
  if (!el) return;
  el.innerHTML = '<div class="ins-empty-state" style="padding:40px 0"><div style="color:var(--text3);font-size:13px">Loading archive…</div></div>';
  try {
    const reports = await apiFetch('/insights/reports');
    if (!reports.length) {
      el.innerHTML = '<div class="ins-empty-state" style="padding:40px 0"><div style="color:var(--text3);font-size:13px">No archived reports yet. Reports are generated automatically each week.</div></div>';
      return;
    }
    el.innerHTML = reports.map(r => {
      const ws      = new Date(r.week_start);
      const we      = new Date(r.week_end);
      const weekEnd = new Date(we.getTime() - 86400000);
      const lbl     = ws.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ' – ' +
        weekEnd.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
      const delta   = r.score_delta;
      const deltaHtml = (delta === null || delta === undefined) ? '' :
        `<span class="ins-archive-delta ins-delta-${delta >= 0 ? 'up' : 'down'}">${delta >= 0 ? '▲' : '▼'} ${Math.abs(delta).toFixed(1)}%</span>`;
      return `<div class="ins-archive-row" onclick="loadInsightArchiveReport('${r.id}')">
        <div class="ins-archive-week">${esc(lbl)}</div>
        <div class="ins-archive-meta">
          <span class="ins-grade-badge ins-grade-${_insGradeColor(r.grade)} ins-grade-sm">${esc(r.grade)}</span>
          <span class="ins-archive-score">${r.reliability_score.toFixed(1)}%</span>
          ${deltaHtml}
        </div>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="color:var(--text3)"><polyline points="9 18 15 12 9 6"/></svg>
      </div>`;
    }).join('');
  } catch (e) {
    el.innerHTML = '<div class="ins-empty-state" style="padding:40px 0"><div style="color:var(--danger);font-size:13px">Failed to load archive.</div></div>';
  }
}

async function loadInsightArchiveReport(id) {
  switchInsightTab('this-week');
  _showInsLoading(true);
  try {
    const r = await apiFetch('/insights/reports/' + id);
    _insightReport = r;
    _insightMessages = [];
    _renderInsightReport(r);
  } catch (e) {
    toast('Failed to load report: ' + e.message, 'error');
    _showInsLoading(false);
  }
}

async function sendInsightQuestion() {
  if (!_insightReport) return;
  const input = document.getElementById('ins-chat-input');
  const q = (input?.value || '').trim();
  if (!q) return;
  input.value = '';
  _insightMessages.push({ role: 'user', content: q });
  _insChatAppend('user', q);
  const thinking = _insChatAppend('assistant', '…');
  try {
    const res = await apiFetch('/insights/reports/' + _insightReport.id + '/ask', {
      method: 'POST',
      body: JSON.stringify({ messages: _insightMessages }),
    });
    if (thinking) thinking.textContent = res.answer;
    _insightMessages.push({ role: 'assistant', content: res.answer });
  } catch (e) {
    if (thinking) thinking.textContent = 'Could not get an answer: ' + e.message;
  }
}

function askInsightPreset(q) {
  const input = document.getElementById('ins-chat-input');
  if (input) { input.value = q; sendInsightQuestion(); }
}

function _insChatAppend(role, text) {
  const container = document.getElementById('ins-chat-messages');
  if (!container) return null;
  const el = document.createElement('div');
  el.className = 'ins-chat-msg ins-chat-' + role;
  el.textContent = text;
  container.appendChild(el);
  container.scrollTop = container.scrollHeight;
  return el;
}
