'use strict';

const _LOG_POOL = [
  { type: 'ok',    event: 'payment.succeeded',              ms: '2ms' },
  { type: 'ok',    event: 'order.created',                  ms: '4ms' },
  { type: 'ok',    event: 'user.signup',                    ms: '3ms' },
  { type: 'retry', event: 'charge.failed — retry 1/5' },
  { type: 'ok',    event: 'charge.failed — delivered', ms: '6ms' },
  { type: 'ok',    event: 'invoice.paid',                   ms: '1ms' },
  { type: 'retry', event: 'webhook.timeout — retry 1/5' },
  { type: 'ok',    event: 'webhook.timeout — delivered', ms: '8ms' },
  { type: 'ok',    event: 'subscription.updated',           ms: '3ms' },
  { type: 'ok',    event: 'customer.created',               ms: '2ms' },
  { type: 'retry', event: 'delivery.failed — retry 2/5' },
  { type: 'ok',    event: 'delivery.failed — delivered', ms: '11ms' },
  { type: 'ok',    event: 'refund.initiated',               ms: '5ms' },
  { type: 'ok',    event: 'shipment.dispatched',            ms: '2ms' },
  { type: 'ok',    event: 'trial.started',                  ms: '3ms' },
];
const _LOG_MAX = 8;

function startLogFeed(container) {
  let idx = 0;

  function addRow() {
    const ev = _LOG_POOL[idx % _LOG_POOL.length];
    idx++;

    const row = document.createElement('div');
    row.className = 'log-row';
    const iconCls = ev.type === 'ok' ? 'log-ok' : 'log-retry';
    const icon = ev.type === 'ok' ? '✓' : '↺';
    const eventCls = ev.type === 'retry' ? ' log-retry' : '';
    row.innerHTML =
      `<span class="log-icon ${iconCls}">${icon}</span>` +
      `<span class="log-event${eventCls}">${ev.event}</span>` +
      (ev.ms ? `<span class="log-ms">${ev.ms}</span>` : '');
    container.appendChild(row);

    // Fade out and remove oldest row when over limit
    const rows = container.querySelectorAll('.log-row:not(.fading)');
    if (rows.length > _LOG_MAX) {
      const oldest = rows[0];
      oldest.classList.add('fading');
      setTimeout(() => oldest.remove(), 240);
    }

    const delay = ev.type === 'retry' ? 480 : 320 + Math.random() * 480;
    setTimeout(addRow, delay);
  }

  // Seed initial rows instantly (no animation delay)
  _LOG_POOL.slice(0, 6).forEach((ev, i) => {
    setTimeout(() => addRow(), i * 90);
  });
}

document.addEventListener('DOMContentLoaded', () => {
  const { ReloraMock: data, ReloraUtils: utils } = window;

  const features = utils.byId('feature-grid');
  if (features) {
    features.innerHTML = data.features.map(([title, body]) => `
      <article class="feature-card" data-reveal>
        <div class="feature-icon">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M20 6 9 17l-5-5"></path>
          </svg>
        </div>
        <h3>${title}</h3>
        <p>${body}</p>
      </article>
    `).join('');
  }

  const logBody = utils.byId('landing-log-body');
  if (logBody) startLogFeed(logBody);

  utils.observeReveal();
});
