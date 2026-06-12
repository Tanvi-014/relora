'use strict';

// Guided Feature Tour — runs after demo completes
// Walks users through key UI features with interactive popovers

const RELORA_TOUR_KEY = 'relora_tour_state_v1';
const RELORA_TOUR_DONE = 'relora_tour_completed_v1';

const RELORA_TOUR_STEPS = [
  {
    id: 'dashboard-kpis',
    title: 'Real-time Metrics',
    body: 'Your dashboard shows success rate, latency, and pending queues at a glance. These update live as events flow through Relora.',
    target: '.mc-kpi-strip',
    position: 'bottom',
    highlight: true,
  },
  {
    id: 'event-feed',
    title: 'Event Feed',
    body: 'Watch your event pipeline in real-time. Green = delivered, orange = retrying, red = failed. Click any event to see full details.',
    target: '#mc-stream-body',
    position: 'left',
    highlight: true,
  },
  {
    id: 'destinations',
    title: 'Destination Health',
    body: 'See which endpoints are healthy, degraded, or having issues. Circuit breakers automatically pause traffic to failing destinations.',
    target: '#mc-dest-list',
    position: 'left',
    highlight: true,
  },
  {
    id: 'dlq-indicator',
    title: 'Dead Letter Queue',
    body: 'Events that fail all retries move to the DLQ. You can inspect and replay them from the Recovery Center.',
    target: '#mc-dlq-block, [data-page="dlq-intelligence"]',
    position: 'bottom',
    highlight: true,
  },
  {
    id: 'nav-recovery',
    title: 'Recovery Center',
    body: 'Manage failed events, incidents, and replays. This is where you investigate and recover from delivery failures.',
    target: '[data-page="recovery"]',
    position: 'right',
    highlight: false,
    action: 'navTo("recovery")',
  },
  {
    id: 'nav-alerts',
    title: 'Alert Setup',
    body: 'Set up notifications for failures, DLQ depth, and circuit breaker trips. Stay on top of issues before they impact production.',
    target: '[data-page="settings"]',
    position: 'right',
    highlight: false,
    action: 'navTo("settings")',
  },
  {
    id: 'nav-webhooks',
    title: 'Webhook History',
    body: 'Search and filter all events sent through your project. See detailed retry attempts, response codes, and payload history.',
    target: '[data-page="webhooks"]',
    position: 'right',
    highlight: false,
    action: 'navTo("webhooks")',
  },
  {
    id: 'complete',
    title: 'You're All Set! 🎉',
    body: 'You now know how to monitor webhooks, handle failures, and recover from incidents. Start sending real events and Relora will handle the rest.',
    target: '#onboarding-section, .logo',
    position: 'center',
    highlight: false,
    action: 'none',
  },
];

let reloraTourTimers = [];
let reloraTourOverlay = null;
let reloraTourPopover = null;

function reloraTourRead() {
  try { return JSON.parse(localStorage.getItem(RELORA_TOUR_KEY) || '{}'); } catch { return {}; }
}

function reloraTourWrite(state) {
  localStorage.setItem(RELORA_TOUR_KEY, JSON.stringify(state));
}

function reloraTourShouldAutoStart() {
  // Only start if demo just completed AND tour hasn't been done yet
  if (localStorage.getItem(RELORA_TOUR_DONE) === '1') return false;
  if (localStorage.getItem(RELORA_DEMO_DONE) !== '1') return false;
  // Don't auto-start if there's a real (non-sandbox) destination already
  if (window._reloraHasRealDestinations) return false;
  return !reloraTourRead().startedAt;
}

function reloraTourStart({ force = false } = {}) {
  reloraTourCleanup();
  if (force) localStorage.removeItem(RELORA_TOUR_DONE);
  const now = Date.now();
  reloraTourWrite({ startedAt: now, activeIndex: 0, complete: false });
  reloraTourShowStep(0);
}

function reloraTourNext() {
  const state = reloraTourRead();
  const nextIndex = (state.activeIndex || 0) + 1;
  if (nextIndex >= RELORA_TOUR_STEPS.length) {
    reloraTourComplete();
    return;
  }
  state.activeIndex = nextIndex;
  reloraTourWrite(state);
  reloraTourShowStep(nextIndex);
}

function reloraTourPrev() {
  const state = reloraTourRead();
  const prevIndex = Math.max(0, (state.activeIndex || 0) - 1);
  state.activeIndex = prevIndex;
  reloraTourWrite(state);
  reloraTourShowStep(prevIndex);
}

function reloraTourSkip() {
  reloraTourCleanup();
  localStorage.setItem(RELORA_TOUR_DONE, '1');
}

function reloraTourComplete() {
  reloraTourCleanup();
  localStorage.setItem(RELORA_TOUR_DONE, '1');
  toast('Tour complete! You're ready to go live with real webhooks.', 'success');
}

function reloraTourCleanup() {
  reloraTourTimers.forEach(clearTimeout);
  reloraTourTimers = [];
  if (reloraTourOverlay) {
    reloraTourOverlay.remove();
    reloraTourOverlay = null;
  }
  if (reloraTourPopover) {
    reloraTourPopover.remove();
    reloraTourPopover = null;
  }
}

function reloraTourShowStep(index) {
  const step = RELORA_TOUR_STEPS[index];
  if (!step) return;

  reloraTourCleanup();

  // Execute action if defined
  if (step.action && step.action !== 'none') {
    eval(step.action);
  }

  // Small delay to let nav settle
  const timer = setTimeout(() => {
    const targetEl = document.querySelector(step.target);
    if (!targetEl) {
      console.warn(`Tour target not found: ${step.target}`);
      reloraTourNext();
      return;
    }

    // Create highlight overlay if requested
    if (step.highlight) {
      reloraTourCreateHighlight(targetEl);
    }

    // Create and position popover
    reloraTourCreatePopover(step, targetEl, index);
  }, step.action ? 600 : 100);

  reloraTourTimers.push(timer);
}

function reloraTourCreateHighlight(targetEl) {
  const overlay = document.createElement('div');
  overlay.className = 'tour-overlay';
  overlay.id = 'tour-overlay';

  const canvas = document.createElement('canvas');
  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;
  overlay.appendChild(canvas);

  const rect = targetEl.getBoundingClientRect();
  const padding = 8;
  const ctx = canvas.getContext('2d');

  // Dark overlay
  ctx.fillStyle = 'rgba(0, 0, 0, 0.6)';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  // Clear highlight area
  ctx.clearRect(
    rect.left - padding,
    rect.top - padding,
    rect.width + padding * 2,
    rect.height + padding * 2
  );

  // Draw border
  ctx.strokeStyle = 'rgba(255, 255, 255, 0.8)';
  ctx.lineWidth = 2;
  ctx.strokeRect(
    rect.left - padding,
    rect.top - padding,
    rect.width + padding * 2,
    rect.height + padding * 2
  );

  document.body.appendChild(overlay);
  reloraTourOverlay = overlay;
}

function reloraTourCreatePopover(step, targetEl, index) {
  const popover = document.createElement('div');
  popover.className = 'tour-popover';
  popover.id = 'tour-popover';

  const isLast = index === RELORA_TOUR_STEPS.length - 1;
  const navHtml = `
    <div class="tour-nav">
      ${index > 0 ? '<button class="btn btn-secondary btn-xs" onclick="reloraTourPrev()">← Back</button>' : ''}
      <span class="tour-counter">${index + 1} / ${RELORA_TOUR_STEPS.length}</span>
      ${!isLast ? '<button class="btn btn-primary btn-xs" onclick="reloraTourNext()">Next →</button>' : '<button class="btn btn-primary btn-xs" onclick="reloraTourComplete()">Done</button>'}
      <button class="tour-close" onclick="reloraTourSkip()" title="Skip tour">&times;</button>
    </div>
  `;

  popover.innerHTML = `
    <div class="tour-head">
      <strong>${step.title}</strong>
    </div>
    <div class="tour-body">
      <p>${step.body}</p>
    </div>
    ${navHtml}
  `;

  document.body.appendChild(popover);

  // Position popover relative to target
  const rect = targetEl.getBoundingClientRect();
  const margin = 16;
  let top = rect.bottom + margin;
  let left = Math.max(margin, Math.min(rect.left + rect.width / 2 - 160, window.innerWidth - 320 - margin));

  if (step.position === 'center') {
    top = window.innerHeight / 2 - 100;
    left = (window.innerWidth - 320) / 2;
  } else if (step.position === 'left') {
    left = rect.left - 320 - margin;
    top = rect.top + rect.height / 2 - 80;
  } else if (step.position === 'right') {
    left = rect.right + margin;
    top = rect.top + rect.height / 2 - 80;
  } else if (step.position === 'top') {
    top = rect.top - 180 - margin;
    left = Math.max(margin, Math.min(rect.left + rect.width / 2 - 160, window.innerWidth - 320 - margin));
  }

  popover.style.top = Math.max(margin, Math.min(top, window.innerHeight - 200 - margin)) + 'px';
  popover.style.left = left + 'px';

  reloraTourPopover = popover;
}

// Integrate with demo completion
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    window.reloraTourStart = reloraTourStart;
    window.reloraTourNext = reloraTourNext;
    window.reloraTourPrev = reloraTourPrev;
    window.reloraTourSkip = reloraTourSkip;
  });
} else {
  window.reloraTourStart = reloraTourStart;
  window.reloraTourNext = reloraTourNext;
  window.reloraTourPrev = reloraTourPrev;
  window.reloraTourSkip = reloraTourSkip;
}
