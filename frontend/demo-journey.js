'use strict';

const RELORA_DEMO_KEY = 'relora_demo_journey_state_v1';
const RELORA_DEMO_DONE = 'relora_demo_journey_completed_v1';

const RELORA_DEMO_STEPS = [
  { key: 'receive', label: 'Receive event', title: 'Relora accepted a webhook', body: 'A Stripe invoice.paid event arrived. Relora returned 200 immediately and persisted the payload.', stream: ['ACCEPTED', 'stripe.invoice.paid', 'evt_demo_7a91'], tone: 'info', delay: 2200 },
  { key: 'deliver', label: 'Deliver event', title: 'First destination delivered', body: 'The billing endpoint acknowledged the event on the first attempt.', stream: ['DELIVERED', 'billing-webhook', '200 in 184 ms'], tone: 'ok', delay: 6200 },
  { key: 'fail', label: 'Fail event', title: 'Second destination failed', body: 'The analytics mirror returned 500. Relora scheduled retries with backoff instead of losing the event.', stream: ['FAILED', 'analytics-mirror', '500 server error'], tone: 'err', delay: 10600 },
  { key: 'dlq', label: 'Move to DLQ', title: 'Retries exhausted, event moved to DLQ', body: 'Relora classified the failure as SERVER_ERROR and opened an incident for the destination.', stream: ['DLQ', 'analytics-mirror', 'SERVER_ERROR'], tone: 'err', delay: 15800 },
  { key: 'replay', label: 'Replay event', title: 'Replay started safely', body: 'A controlled replay re-queued the failed delivery at 60 events/min so the recovering destination is protected.', stream: ['REPLAY', 'evt_demo_7a91', '60/min governor'], tone: 'warn', delay: 22200 },
  { key: 'recover', label: 'Recover event', title: 'Recovered delivery', body: 'The replay succeeded. The DLQ cleared, the incident moved to recovering, and the full path remains auditable.', stream: ['RECOVERED', 'analytics-mirror', '200 in 211 ms'], tone: 'ok', delay: 29200 },
];

let reloraDemoTimers = [];
let reloraDemoRenderTick = null;

function reloraDemoRead() {
  try { return JSON.parse(localStorage.getItem(RELORA_DEMO_KEY) || '{}'); } catch { return {}; }
}

function reloraDemoWrite(state) {
  localStorage.setItem(RELORA_DEMO_KEY, JSON.stringify(state));
}

function reloraDemoShouldAutoStart() {
  if (localStorage.getItem(RELORA_DEMO_DONE) === '1') return false;
  return !reloraDemoRead().startedAt;
}

function reloraDemoStart({ force = false } = {}) {
  reloraDemoTimers.forEach(clearTimeout);
  reloraDemoTimers = [];
  if (reloraDemoRenderTick) clearInterval(reloraDemoRenderTick);
  if (force) localStorage.removeItem(RELORA_DEMO_DONE);
  const now = Date.now();
  reloraDemoWrite({ startedAt: now, activeIndex: 0, complete: false });
  reloraDemoRender(0);
  RELORA_DEMO_STEPS.forEach((step, index) => {
    const timer = setTimeout(() => {
      reloraDemoWrite({ startedAt: now, activeIndex: index, complete: index === RELORA_DEMO_STEPS.length - 1 });
      reloraDemoRender(index);
      if (index === RELORA_DEMO_STEPS.length - 1) localStorage.setItem(RELORA_DEMO_DONE, '1');
    }, force && index === 0 ? 0 : step.delay);
    reloraDemoTimers.push(timer);
  });
  reloraDemoRenderTick = setInterval(() => {
    const state = reloraDemoRead();
    if (!state.startedAt || state.complete) {
      clearInterval(reloraDemoRenderTick);
      reloraDemoRenderTick = null;
      reloraDemoRender();
      return;
    }
    reloraDemoRender();
  }, 1800);
}

function reloraDemoReset() {
  reloraDemoTimers.forEach(clearTimeout);
  reloraDemoTimers = [];
  if (reloraDemoRenderTick) {
    clearInterval(reloraDemoRenderTick);
    reloraDemoRenderTick = null;
  }
  localStorage.removeItem(RELORA_DEMO_KEY);
  localStorage.removeItem(RELORA_DEMO_DONE);
  reloraDemoStart({ force: true });
}

function reloraDemoCurrentIndex() {
  const state = reloraDemoRead();
  if (!state.startedAt) return -1;
  if (Number.isInteger(state.activeIndex)) return state.activeIndex;
  const elapsed = Date.now() - state.startedAt;
  let idx = 0;
  RELORA_DEMO_STEPS.forEach((step, index) => { if (elapsed >= step.delay) idx = index; });
  return idx;
}

function reloraDemoRender(index = reloraDemoCurrentIndex()) {
  if (index < 0) return;
  reloraDemoRenderGuide(index);
  reloraDemoRenderStream(index);
  reloraDemoRenderMetrics(index);
  reloraDemoRenderDestinations(index);
  reloraDemoRenderRecovery(index);
}

function reloraDemoRenderGuide(index) {
  const section = document.getElementById('onboarding-section');
  if (!section) return;
  const active = RELORA_DEMO_STEPS[index] || RELORA_DEMO_STEPS[0];
  section.style.display = '';
  section.className = `demo-journey demo-journey--${active.tone}`;
  section.innerHTML = `
    <div class="demo-journey-head">
      <span class="demo-eyebrow">Guided webhook journey</span>
      <strong>${active.title}</strong>
      <span>${active.body}</span>
      <div class="demo-actions">
        <button class="btn btn-primary btn-sm" onclick="reloraDemoStart({ force: true })">Replay demo</button>
        <button class="btn btn-secondary btn-sm" onclick="navTo('recovery')">Open recovery center</button>
      </div>
    </div>
    <div class="demo-steps">
      ${RELORA_DEMO_STEPS.map((step, stepIndex) => `
        <div class="demo-step ${stepIndex < index ? 'done' : stepIndex === index ? 'active' : ''}">
          <span>${stepIndex + 1}</span>
          <strong>${step.label}</strong>
        </div>`).join('')}
    </div>`;
}

function reloraDemoRows(index) {
  return RELORA_DEMO_STEPS.slice(0, index + 1).map((step, i) => {
    const [type, what, where] = step.stream;
    const time = new Date(Date.now() - (index - i) * 41000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    return { type, what, where, time, tone: step.tone };
  }).reverse();
}

function reloraDemoRenderStream(index) {
  const body = document.getElementById('mc-stream-body');
  if (!body) return;
  body.innerHTML = reloraDemoRows(index).map((row, rowIndex) => `
    <div class="mc-event demo-event ${rowIndex === 0 ? 'mc-event--new' : ''}">
      <span class="mc-event-time">${row.time}</span>
      <span class="mc-event-type ev-${row.tone === 'ok' ? 'ok' : row.tone === 'err' ? 'err' : row.tone === 'warn' ? 'warn' : 'info'}">${row.type}</span>
      <span class="mc-event-body">
        <span class="mc-event-what">${row.what}</span>
        <span class="mc-event-arr">-></span>
        <span class="mc-event-where">${row.where}</span>
      </span>
    </div>`).join('');
}

function reloraDemoSet(id, value, color = '') {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = value;
  el.style.color = color;
}

function reloraDemoRenderMetrics(index) {
  const delivered = index >= 5 ? 2 : index >= 1 ? 1 : 0;
  const failures = index >= 2 && index < 5 ? 1 : 0;
  const dlq = index >= 3 && index < 5 ? 1 : 0;
  const success = index >= 5 ? '100%' : index >= 2 ? '50%' : index >= 1 ? '100%' : '-';

  reloraDemoSet('mc-kpi-rate', success, failures ? 'var(--warn)' : 'var(--success)');
  reloraDemoSet('mc-kpi-failures', String(failures), failures ? 'var(--danger)' : '');
  reloraDemoSet('mc-kpi-dlq-strip', String(dlq), dlq ? 'var(--danger)' : '');
  reloraDemoSet('mc-kpi-dests', '2');
  reloraDemoSet('pp-rate', success);
  reloraDemoSet('pp-dlq', String(dlq), dlq ? 'var(--danger)' : '');
  reloraDemoSet('pp-incidents', dlq ? '1' : '0', dlq ? 'var(--danger)' : '');
  reloraDemoSet('pp-delivered', String(delivered));

  const banner = document.getElementById('mc-banner');
  const signal = document.getElementById('mc-signal-dot');
  const text = document.getElementById('mc-signal-text');
  const meta = document.getElementById('mc-banner-meta');
  if (banner && signal && text && meta) {
    const bad = index >= 2 && index < 5;
    banner.className = `mc-banner ${bad ? 'banner-warn' : 'banner-ok'} demo-owned`;
    signal.className = `mc-signal-dot ${bad ? 'warn' : 'ok'}`;
    text.className = `mc-signal-text ${bad ? 'text-warn' : 'text-ok'}`;
    text.textContent = index >= 5 ? 'Recovered' : bad ? 'Demo failure in progress' : 'Demo pipeline healthy';
    meta.innerHTML = `
      <span class="mc-meta-item">${delivered} delivered</span>
      <span class="mc-meta-item ${failures ? 'meta-crit' : ''}">${failures} failed</span>
      <span class="mc-meta-item ${dlq ? 'meta-crit' : ''}">${dlq} in DLQ</span>
      <span class="mc-meta-item mc-meta-muted">simulated, not real traffic</span>`;
  }

  const dlqBlock = document.getElementById('mc-dlq-block');
  const dlqNum = document.getElementById('mc-dlq-num');
  if (dlqBlock && dlqNum) {
    dlqBlock.style.display = dlq ? '' : 'none';
    dlqNum.textContent = String(dlq);
  }
}

function reloraDemoRenderDestinations(index) {
  const list = document.getElementById('mc-dest-list');
  if (!list) return;
  const mirrorState = index >= 5 ? ['healthy', 'closed', '100%'] : index >= 2 ? ['unhealthy', 'open', '0%'] : ['healthy', 'closed', '-'];
  list.innerHTML = `
    <div class="mc-dest-row demo-owned"><span class="mc-dest-dot healthy"></span><span class="mc-dest-name">billing-webhook</span><span class="mc-dest-cb circuit-closed">closed</span><span class="mc-dest-rate">100%</span></div>
    <div class="mc-dest-row demo-owned"><span class="mc-dest-dot ${mirrorState[0]}"></span><span class="mc-dest-name">analytics-mirror</span><span class="mc-dest-cb circuit-${mirrorState[1]}">${mirrorState[1]}</span><span class="mc-dest-rate ${mirrorState[2] === '0%' ? 'rate-bad' : ''}">${mirrorState[2]}</span></div>`;
}

function reloraDemoRenderRecovery(index) {
  const activeFailure = index >= 2 && index < 5;
  const dlq = index >= 3 && index < 5;
  const recovered = index >= 5;
  const failures = document.getElementById('recovery-failures-body');
  if (failures) {
    failures.innerHTML = activeFailure || recovered
      ? `<table><thead><tr><th>Time</th><th>Destination</th><th>Category</th><th></th></tr></thead><tbody><tr><td style="font-family:var(--mono);font-size:12px;color:var(--text3)">demo</td><td>analytics-mirror</td><td><span style="font-family:var(--mono);font-size:11px;color:${recovered ? 'var(--success)' : 'var(--danger)'}">${recovered ? 'RECOVERED' : 'SERVER_ERROR'}</span></td><td><button class="btn btn-xs btn-secondary" onclick="navTo('dlq-intelligence')">Investigate</button></td></tr></tbody></table>`
      : `<div class="empty-state"><div class="title">No recent failures</div><div class="desc">The demo will create a failed delivery here.</div></div>`;
  }
  const incidents = document.getElementById('recovery-incidents-body');
  if (incidents) {
    incidents.innerHTML = activeFailure || recovered
      ? `<table><thead><tr><th>Severity</th><th>Category</th><th>Destination</th><th>Affected</th><th>State</th></tr></thead><tbody><tr><td><span style="font-family:var(--mono);font-size:10px;color:${recovered ? 'var(--success)' : 'var(--danger)'}">${recovered ? 'LOW' : 'HIGH'}</span></td><td>SERVER_ERROR</td><td>analytics-mirror</td><td>1</td><td><span class="badge ${recovered ? 'completed' : 'failed'}">${recovered ? 'RECOVERING' : 'OPEN'}</span></td></tr></tbody></table>`
      : `<div class="empty-state"><div class="title">No active incidents</div><div class="desc">Incidents appear when failures share a root cause.</div></div>`;
  }
  const dlqBody = document.getElementById('recovery-dlq-body');
  if (dlqBody) {
    dlqBody.innerHTML = dlq
      ? `<div class="demo-dlq-callout"><strong>1 event in Dead Letter Queue</strong><span>analytics-mirror failed all retry attempts. Replay is now the recovery action.</span><button class="btn btn-primary btn-sm" onclick="navTo('replay')">Replay event</button></div>`
      : `<div class="empty-state"><div class="title">${recovered ? 'DLQ cleared' : 'DLQ is empty'}</div><div class="desc">${recovered ? 'Replay recovered the failed event.' : 'The demo will move a failed event here.'}</div></div>`;
  }
  const replayBody = document.getElementById('recovery-replays-body');
  if (replayBody) {
    replayBody.innerHTML = index >= 4
      ? `<table><thead><tr><th>Started</th><th>Status</th><th>Total</th><th>Delivered</th><th>Failed</th></tr></thead><tbody><tr><td>demo</td><td><span class="badge ${recovered ? 'completed' : 'processing'}">${recovered ? 'completed' : 'running'}</span></td><td>1</td><td>${recovered ? '1' : '0'}</td><td>${recovered ? '0' : '-'}</td></tr></tbody></table>`
      : `<div class="empty-state"><div class="title">No replay history</div><div class="desc">Replay begins after the event reaches DLQ.</div></div>`;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  window.reloraDemoStart = reloraDemoStart;
  window.reloraDemoReset = reloraDemoReset;
  setTimeout(() => {
    if (reloraDemoShouldAutoStart()) reloraDemoStart();
    else reloraDemoRender();
  }, 900);

  const originalNav = window.navTo;
  if (typeof originalNav === 'function' && !originalNav.__reloraDemoWrapped) {
    window.navTo = function demoAwareNav(page) {
      originalNav(page);
      setTimeout(() => reloraDemoRender(), 80);
    };
    window.navTo.__reloraDemoWrapped = true;
  }
});
