'use strict';

window.ReloraUtils = {
  compactNumber(value) {
    return new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(value);
  },
  formatMetric(metric) {
    const value = metric.value > 999 ? this.compactNumber(metric.value) : metric.value;
    return `${value}${metric.suffix || ''}`;
  },
  byId(id) {
    return document.getElementById(id);
  },
  staggerIn(selector) {
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    document.querySelectorAll(selector).forEach((el, index) => {
      if (reduce) {
        el.classList.add('is-visible');
        return;
      }
      setTimeout(() => el.classList.add('is-visible'), index * 70);
    });
  },
  observeReveal() {
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const nodes = document.querySelectorAll('[data-reveal]');
    if (reduce || !('IntersectionObserver' in window)) {
      nodes.forEach((node) => node.classList.add('is-visible'));
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-visible');
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.18 });
    nodes.forEach((node) => observer.observe(node));
  },
};
