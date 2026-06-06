'use strict';

document.addEventListener('DOMContentLoaded', () => {
  const { ReloraMock: data, ReloraUtils: utils } = window;

  const kpis = utils.byId('kpi-strip');
  if (kpis) {
    kpis.innerHTML = data.metrics.map((metric) => `
      <div class="kpi">
        <div class="kpi-label">${metric.label}</div>
        <div class="kpi-value tone-${metric.tone}">${utils.formatMetric(metric)}</div>
        <div class="kpi-detail">${metric.detail}</div>
      </div>
    `).join('');
  }

  const journey = utils.byId('journey');
  if (journey) {
    journey.innerHTML = data.journey.map((step) => `
      <div class="journey-step ${step.status}">
        <div class="journey-dot"></div>
        <div>
          <div class="journey-title">${step.title}</div>
          <div class="journey-body">${step.body}</div>
        </div>
        <div class="journey-time">${step.time}</div>
      </div>
    `).join('');
  }

  const feed = utils.byId('feed');
  if (feed) {
    feed.innerHTML = data.feed.map((item) => `
      <div class="feed-item">
        <div class="feed-dot ${item.type}"></div>
        <div>
          <div class="feed-title">${item.title}</div>
          <div class="feed-meta">${item.meta}</div>
        </div>
      </div>
    `).join('');
  }

  const anomaly = utils.byId('anomaly');
  if (anomaly) {
    anomaly.innerHTML = data.anomalies.map((item) => `
      <div class="change-row">
        <div class="change-top">
          <strong>${item.label}</strong>
          <span class="change-values">${item.before} -> ${item.after}</span>
        </div>
        <div class="change-note">${item.note}</div>
      </div>
    `).join('');
  }

  const bars = utils.byId('throughput-bars');
  if (bars) {
    bars.innerHTML = data.bars.map((height, index) => (
      `<div class="bar" style="height:${height}%;animation-delay:${index * 35}ms"></div>`
    )).join('');
  }

  utils.observeReveal();
  utils.staggerIn('.metric-row');
});
