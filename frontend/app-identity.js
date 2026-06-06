'use strict';

function reloraApplyProductLanguage() {
  const setText = (selector, value) => {
    const el = document.querySelector(selector);
    if (el) el.textContent = value;
  };
  const setNav = (page, value) => {
    const el = document.querySelector(`.nav-item[data-page="${page}"]`);
    if (!el) return;
    for (const node of el.childNodes) {
      if (node.nodeType === Node.TEXT_NODE && node.textContent.trim()) {
        node.textContent = ` ${value} `;
        return;
      }
    }
  };

  setText('.nav-section:nth-of-type(1) .nav-section-label', 'Operate');
  setNav('webhooks', 'Deliveries');
  setNav('analytics', 'SLOs');
  setNav('replay', 'Replay');
  setNav('alerts', 'Monitoring');

  const sectionLabels = Array.from(document.querySelectorAll('.nav-section-label'));
  const labels = ['Operate', 'Route', 'Recover', 'Account'];
  sectionLabels.forEach((label, index) => {
    if (labels[index]) label.textContent = labels[index];
  });

  if (!document.querySelector('.nav-item[data-page="audit"]')) {
    const replayNav = document.querySelector('.nav-item[data-page="replay"]');
    const auditNav = document.createElement('div');
    auditNav.className = 'nav-item';
    auditNav.dataset.page = 'audit';
    auditNav.setAttribute('onclick', "navTo('audit')");
    auditNav.innerHTML = `
      <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
        <path d="M14 2v6h6"/><path d="M8 13h8"/><path d="M8 17h5"/>
      </svg>
      Audit Log`;
    replayNav?.after(auditNav);
  }

  setText('#page-webhooks .page-title', 'Deliveries');
  setText('#page-webhooks .page-subtitle', 'Every accepted event, every destination attempt, and the exact retry path. Inspect a row to answer: did it arrive, why not, and what happens next?');
  setText('#page-webhooks .empty-state .title', 'No deliveries recorded');
  setText('#page-webhooks .empty-state .desc', 'Send a test webhook after defining a destination. Relora will show the accepted event, each attempt, and its final outcome here.');

  setText('#page-destinations .page-subtitle', 'The endpoints Relora is responsible for protecting. Each route owns retry policy, filtering, signing, ordering, circuit state, and SLO expectations.');
  setText('#page-alerts .page-title', 'Monitoring');
  setText('#page-alerts .page-subtitle', 'Escalate delivery degradation, DLQ growth, circuit breaker opens, and schema changes to the teams that own recovery.');
  setText('#page-analytics .page-title', 'SLOs');
  setText('#page-analytics .page-subtitle', 'Delivery health by destination: success rate, latency percentiles, backlog, and circuit breaker posture.');
  setText('#page-replay .page-title', 'Replay');
  setText('#page-replay .page-subtitle', 'Re-queue a controlled time window after an incident. Rate limits keep recovering destinations from getting knocked over again.');
  setText('#page-settings .page-subtitle', 'Project credentials, ingest URL, and irreversible operational controls.');
  setText('#page-dlq-intelligence .page-subtitle', 'A recovery workbench for permanently failed deliveries: classification, incidents, root causes, and replay candidates.');

  setText('.onboarding-title', 'Build the first delivery route.');
  setText('.onboarding-desc', 'Relora starts useful when it can accept an event, persist it, and prove where it went. Connect one endpoint, copy the ingest URL, then send a test payload through the full pipeline.');
  const onboardingSteps = document.querySelectorAll('.onboarding-step');
  ['Define destination', 'Copy ingest URL', 'Send test event'].forEach((text, index) => {
    const step = onboardingSteps[index];
    if (!step) return;
    const num = step.querySelector('.onboarding-step-num')?.outerHTML || '';
    step.innerHTML = `${num}${text}`;
  });

  if (!document.getElementById('page-audit')) {
    const audit = document.createElement('div');
    audit.className = 'page';
    audit.id = 'page-audit';
    audit.innerHTML = `
      <div class="page-title">Audit Log</div>
      <div class="page-subtitle">A chronological ledger of operational changes: route edits, key rotations, replay jobs, alert changes, and project membership.</div>
      <div class="table-toolbar" style="margin-bottom:12px">
        <select id="audit-type-filter" class="select" style="width:170px" onchange="loadAuditLog()">
          <option value="">All resource types</option>
          <option value="destination">Destination</option>
          <option value="alert_config">Alert</option>
          <option value="webhook">Webhook</option>
          <option value="project">Project</option>
          <option value="api_key">API Key</option>
          <option value="team_member">Team Member</option>
        </select>
        <select id="audit-action-filter" class="select" style="width:140px" onchange="loadAuditLog()">
          <option value="">All actions</option>
          <option value="CREATE">Create</option>
          <option value="UPDATE">Update</option>
          <option value="DELETE">Delete</option>
          <option value="REPLAY">Replay</option>
        </select>
        <button class="btn btn-secondary btn-sm" onclick="loadAuditLog()">Refresh</button>
      </div>
      <div class="table-wrap" id="audit-table-wrap">
        <div style="padding:40px;text-align:center;color:var(--text3);font-size:13px">Loading…</div>
      </div>
      <div class="pagination" id="audit-pagination"></div>`;
    document.getElementById('content-area')?.appendChild(audit);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  reloraApplyProductLanguage();
  if (typeof window.navTo === 'function' && !window.navTo.__reloraIdentityWrapped) {
    const originalNavTo = window.navTo;
    window.navTo = function reloraNavTo(page) {
      originalNavTo(page);
      reloraApplyProductLanguage();
    };
    window.navTo.__reloraIdentityWrapped = true;
  }
});
