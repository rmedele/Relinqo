/* Relinqo shared shell — toast notifications, command palette, keyboard shortcuts.
   Loaded on every authenticated page. Exposes window.LR for easy use from page-specific JS. */
(function () {
  'use strict';

  // ---------------------- Visual system ----------------------
  function bootVisualSystem() {
    document.documentElement.classList.add('lr-visuals-ready');

    const texture = document.createElement('div');
    texture.className = 'lr-scene-texture';
    texture.setAttribute('aria-hidden', 'true');
    document.body.prepend(texture);

    const candidates = document.querySelectorAll(
      '.card, .stat-card, .chart-card, .priority-card, .lead-item, .kanban-card, .mk-demo-card, .mk-price-card, .mk-benefit, .mk-step, .mk-faq, .mk-stat, .auth-card, .settings-card'
    );
    candidates.forEach((el, index) => {
      el.classList.add('lr-reveal');
      el.style.setProperty('--reveal-delay', `${Math.min(index % 8, 7) * 55}ms`);
    });

    if (!('IntersectionObserver' in window)) {
      candidates.forEach((el) => el.classList.add('is-visible'));
      return;
    }

    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add('is-visible');
        observer.unobserve(entry.target);
      });
    }, { threshold: 0.08, rootMargin: '0px 0px -8% 0px' });

    candidates.forEach((el) => observer.observe(el));
  }

  function bootShuttleMotion() {
    const hero = document.querySelector('.mk-hero');
    if (!hero || hero.querySelector('.shuttle-stage')) return;

    const stage = document.createElement('div');
    stage.className = 'shuttle-stage';
    stage.setAttribute('aria-hidden', 'true');
    stage.innerHTML = `
      <div class="shuttle-orbit shuttle-orbit-one"></div>
      <div class="shuttle-orbit shuttle-orbit-two"></div>
      <div class="shuttle-core">
        <div class="shuttle-window-bar"><span></span><span></span><span></span></div>
        <div class="shuttle-file shuttle-file-main">
          <span class="shuttle-file-kicker">Lead packet</span>
          <strong>Emergency job request</strong>
          <i></i><i></i><i></i>
        </div>
        <div class="shuttle-file shuttle-file-reply">
          <span class="shuttle-file-kicker">Draft reply</span>
          <strong>Ready in 42s</strong>
          <i></i><i></i>
        </div>
        <div class="shuttle-file shuttle-file-alert">
          <span class="shuttle-file-kicker">Owner alert</span>
          <strong>High urgency</strong>
          <i></i>
        </div>
        <div class="shuttle-rail">
          <span></span><span></span><span></span><span></span>
        </div>
      </div>
      <div class="shuttle-node shuttle-node-one"></div>
      <div class="shuttle-node shuttle-node-two"></div>
      <div class="shuttle-node shuttle-node-three"></div>
      <div class="relay-core-3d">
        <span></span><span></span><span></span><span></span><span></span><span></span>
      </div>
      <svg class="relay-morph-svg relay-morph-hero" viewBox="0 0 260 260" role="presentation" focusable="false">
        <path class="relay-morph-path" d="M130 13 C190 20 247 68 240 132 C233 196 184 247 121 239 C59 232 16 184 18 124 C20 64 70 6 130 13 Z"></path>
      </svg>
    `;
    const photo = document.createElement('figure');
    photo.className = 'recovery-photo';
    photo.innerHTML = `
      <img src="https://images.unsplash.com/photo-1504307651254-35680f356dfd?auto=format&fit=crop&w=1600&q=82" alt="" loading="eager" />
      <figcaption>
        <span>Recovery desk</span>
        <strong>Every urgent job gets routed before it goes cold.</strong>
      </figcaption>
    `;
    hero.appendChild(photo);
    hero.appendChild(stage);
    hero.classList.add('mk-hero-shuttle');

    let raf = 0;
    const update = () => {
      raf = 0;
      const rect = hero.getBoundingClientRect();
      const viewport = window.innerHeight || 1;
      const progress = Math.min(1, Math.max(0, (viewport - rect.top) / (viewport + rect.height)));
      document.documentElement.style.setProperty('--shuttle-scroll', progress.toFixed(4));
    };
    const requestUpdate = () => {
      if (raf) return;
      raf = requestAnimationFrame(update);
    };
    update();
    window.addEventListener('scroll', requestUpdate, { passive: true });
    window.addEventListener('resize', requestUpdate, { passive: true });
  }

  function bootAugenInteractions() {
    const nav = document.querySelector('.mk-nav');
    if (!nav || nav.dataset.augenReady) return;
    nav.dataset.augenReady = 'true';

    const navLinks = Array.from(nav.querySelectorAll('.mk-nav-links a'));
    const dropdownData = {
      Features: [
        ['01', 'AI reply drafting', 'Classify, summarize, and draft in under a minute.'],
        ['02', 'Urgency routing', 'Escalate burst pipes, roof leaks, and high-value jobs.'],
        ['03', 'Pipeline memory', 'Track every lead from inbox to won revenue.'],
      ],
      'How it works': [
        ['01', 'Connect sources', 'Gmail, forms, missed calls, and booking links.'],
        ['02', 'Tune the voice', 'Services, tone, territory, and approval rules.'],
        ['03', 'Operate live', 'Review, send, schedule, and follow up from one queue.'],
      ],
      Pricing: [
        ['01', 'Starter', 'Lean automation for solo operators.'],
        ['02', 'Pro', 'Unlimited leads, SMS rescue, calendar and pipeline.'],
      ],
      FAQ: [
        ['01', 'Approval rules', 'Choose manual review or confident auto-send.'],
        ['02', 'Safety rails', 'No pricing promises or hard commitments.'],
        ['03', 'Setup', 'Most teams are live the same day.'],
      ],
    };

    navLinks.forEach((link, index) => {
      const label = link.textContent.trim();
      link.classList.add('augen-nav-link');
      link.dataset.index = String(index + 1).padStart(2, '0');
      const panel = document.createElement('div');
      panel.className = 'augen-dropdown';
      panel.innerHTML = `
        <div class="augen-dropdown-title">${escapeHtml(label)}</div>
        <div class="augen-dropdown-grid">
          ${(dropdownData[label] || []).map(([num, title, copy]) => `
            <a href="${link.getAttribute('href')}" class="augen-drop-item">
              <span>${escapeHtml(num)}</span>
              <strong>${escapeHtml(title)}</strong>
              <small>${escapeHtml(copy)}</small>
            </a>
          `).join('')}
        </div>
      `;
      link.parentElement.appendChild(panel);
    });

    const actions = nav.querySelector('.mk-nav-actions');
    if (actions && !actions.querySelector('.augen-search-btn')) {
      const search = document.createElement('button');
      search.type = 'button';
      search.className = 'augen-search-btn';
      search.innerHTML = '<span>05</span> Search';
      actions.insertBefore(search, actions.firstChild);
      search.addEventListener('click', () => openAugenSearch());
    }

    document.querySelectorAll('.mk-benefit, .mk-step, .mk-demo-card, .mk-price-card').forEach((card) => {
      card.classList.add('augen-tilt-card');
    });

    const progress = document.createElement('div');
    progress.className = 'augen-scroll-progress';
    progress.setAttribute('aria-hidden', 'true');
    document.body.appendChild(progress);

    let raf = 0;
    const tick = () => {
      raf = 0;
      const max = Math.max(1, document.documentElement.scrollHeight - window.innerHeight);
      const pct = Math.min(1, Math.max(0, window.scrollY / max));
      document.documentElement.style.setProperty('--augen-progress', pct.toFixed(4));
    };
    const requestTick = () => {
      if (!raf) raf = requestAnimationFrame(tick);
    };
    tick();
    window.addEventListener('scroll', requestTick, { passive: true });
    window.addEventListener('resize', requestTick, { passive: true });
  }

  function bootDepthFields() {
    document.querySelectorAll('.relay-depth-field').forEach((field) => field.remove());
    document.querySelectorAll('.mk-demo-card, .mk-benefit, .mk-step, .mk-price-card, .mk-faq, .mk-stat').forEach((tile) => {
      if (tile.dataset.tileReady) return;
      tile.dataset.tileReady = 'true';
      tile.classList.add('interactive-tile');
      tile.tabIndex = tile.tabIndex < 0 ? 0 : tile.tabIndex;
      tile.addEventListener('click', () => {
        tile.classList.toggle('tile-expanded');
      });
      tile.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          tile.classList.toggle('tile-expanded');
        }
      });
    });
  }

  function openAugenSearch() {
    if (document.querySelector('.augen-search-overlay')) return;
    const overlay = document.createElement('div');
    overlay.className = 'augen-search-overlay';
    overlay.innerHTML = `
      <div class="augen-search-panel" role="dialog" aria-label="Search Relinqo">
        <div class="augen-search-top">
          <span>Search</span>
          <button type="button" aria-label="Close search">&times;</button>
        </div>
        <input type="search" placeholder="Search features, pricing, setup..." autofocus />
        <div class="augen-search-suggestions">
          <a href="#features"><span>01</span> AI lead operations</a>
          <a href="#how-it-works"><span>02</span> Setup flow</a>
          <a href="#pricing"><span>03</span> Pricing plans</a>
          <a href="#faq"><span>04</span> Safety and approval</a>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);
    const close = () => overlay.remove();
    overlay.querySelector('button').addEventListener('click', close);
    overlay.addEventListener('click', (event) => {
      if (event.target === overlay) close();
    });
    overlay.querySelectorAll('a').forEach((link) => link.addEventListener('click', close));
    const keyHandler = (event) => {
      if (event.key === 'Escape') {
        close();
        document.removeEventListener('keydown', keyHandler, true);
      }
    };
    document.addEventListener('keydown', keyHandler, true);
    requestAnimationFrame(() => overlay.querySelector('input')?.focus());
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      bootVisualSystem();
      bootShuttleMotion();
      bootAugenInteractions();
      bootDepthFields();
    }, { once: true });
  } else {
    bootVisualSystem();
    bootShuttleMotion();
    bootAugenInteractions();
    bootDepthFields();
  }

  // ---------------------- Toasts ----------------------
  let toastStack = null;
  function ensureStack() {
    if (toastStack) return toastStack;
    toastStack = document.createElement('div');
    toastStack.className = 'toast-stack';
    document.body.appendChild(toastStack);
    return toastStack;
  }

  const ICONS = {
    success: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>',
    error: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
    info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>',
    warning: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
  };

  function toast(message, kind = 'info', opts = {}) {
    const stack = ensureStack();
    const el = document.createElement('div');
    el.className = `toast ${kind}`;
    const title = opts.title || '';
    el.innerHTML = `
      <span class="toast-icon">${ICONS[kind] || ICONS.info}</span>
      <div class="toast-body">
        ${title ? `<div class="toast-title">${escapeHtml(title)}</div>` : ''}
        <div class="toast-msg">${escapeHtml(message)}</div>
      </div>
      <button class="toast-close" aria-label="Dismiss">&times;</button>
    `;
    stack.appendChild(el);
    const dismiss = () => {
      el.classList.add('leaving');
      setTimeout(() => el.remove(), 320);
    };
    el.querySelector('.toast-close').addEventListener('click', dismiss);
    const timeout = opts.timeout ?? (kind === 'error' ? 7000 : 4000);
    if (timeout > 0) setTimeout(dismiss, timeout);
    return dismiss;
  }

  function escapeHtml(value) {
    if (value == null) return '';
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // ---------------------- Command Palette ----------------------
  let cmdkOverlay = null;
  let cmdkActions = [];
  let cmdkLeads = [];
  let cmdkActiveIdx = 0;
  let cmdkQuery = '';

  const DEFAULT_ACTIONS = [
    { label: 'Go to Queue', section: 'Navigate', run: () => location.href = '/review', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>' },
    { label: 'Go to Pipeline', section: 'Navigate', run: () => location.href = '/pipeline', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="18" rx="1"/><rect x="14" y="3" width="7" height="12" rx="1"/></svg>' },
    { label: 'Go to Analytics', section: 'Navigate', run: () => location.href = '/analytics', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>' },
    { label: 'Go to Templates', section: 'Navigate', run: () => location.href = '/templates', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>' },
    { label: 'Go to Settings', section: 'Navigate', run: () => location.href = '/settings', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/></svg>' },
    { label: 'Show keyboard shortcuts', section: 'Help', run: () => showShortcuts(), icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="4" width="20" height="16" rx="2"/></svg>' },
    { label: 'Logout', section: 'Account', run: async () => { await fetch('/auth/logout', { method: 'POST' }); location.href = '/login'; }, icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>' },
  ];

  function registerActions(actions) { cmdkActions = [...DEFAULT_ACTIONS, ...actions]; }

  async function fetchLeadsForCmdk() {
    if (cmdkLeads.length) return;
    try {
      const res = await fetch('/leads?page=1&page_size=100');
      if (!res.ok) return;
      const data = await res.json();
      cmdkLeads = data.items || [];
    } catch (e) { /* ignore */ }
  }

  function openCmdk() {
    if (cmdkOverlay) return;
    fetchLeadsForCmdk();
    cmdkOverlay = document.createElement('div');
    cmdkOverlay.className = 'cmdk-overlay';
    cmdkOverlay.innerHTML = `
      <div class="cmdk-panel" role="dialog" aria-label="Command palette">
        <div class="cmdk-input-row">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
          <input class="cmdk-input" type="text" placeholder="Search leads or jump to..." autocomplete="off" />
          <span class="cmdk-kbd">esc</span>
        </div>
        <div class="cmdk-results"></div>
      </div>
    `;
    document.body.appendChild(cmdkOverlay);
    const input = cmdkOverlay.querySelector('.cmdk-input');
    input.focus();
    input.addEventListener('input', (e) => { cmdkQuery = e.target.value; cmdkActiveIdx = 0; renderCmdk(); });
    cmdkOverlay.addEventListener('click', (e) => { if (e.target === cmdkOverlay) closeCmdk(); });
    document.addEventListener('keydown', cmdkKeyHandler, true);
    cmdkQuery = '';
    cmdkActiveIdx = 0;
    renderCmdk();
  }

  function closeCmdk() {
    if (!cmdkOverlay) return;
    cmdkOverlay.remove();
    cmdkOverlay = null;
    document.removeEventListener('keydown', cmdkKeyHandler, true);
  }

  function cmdkKeyHandler(e) {
    if (!cmdkOverlay) return;
    if (e.key === 'Escape') { e.preventDefault(); closeCmdk(); return; }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      const items = cmdkOverlay.querySelectorAll('.cmdk-item');
      cmdkActiveIdx = Math.min(items.length - 1, cmdkActiveIdx + 1);
      renderCmdkActive();
      return;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      cmdkActiveIdx = Math.max(0, cmdkActiveIdx - 1);
      renderCmdkActive();
      return;
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      const items = cmdkOverlay.querySelectorAll('.cmdk-item');
      const active = items[cmdkActiveIdx];
      if (active) active.click();
    }
  }

  function renderCmdkActive() {
    const items = cmdkOverlay.querySelectorAll('.cmdk-item');
    items.forEach((it, i) => it.classList.toggle('active', i === cmdkActiveIdx));
    items[cmdkActiveIdx]?.scrollIntoView({ block: 'nearest' });
  }

  function fuzzy(query, str) {
    if (!query) return true;
    return str.toLowerCase().includes(query.toLowerCase());
  }

  function renderCmdk() {
    if (!cmdkOverlay) return;
    const results = cmdkOverlay.querySelector('.cmdk-results');
    const q = cmdkQuery.trim();
    const matchedActions = cmdkActions.filter(a => fuzzy(q, a.label));
    const matchedLeads = q
      ? cmdkLeads.filter(l => fuzzy(q, `${l.sender_name || ''} ${l.sender_email || ''} ${l.subject || ''} ${l.body || ''}`)).slice(0, 8)
      : [];

    let html = '';
    if (matchedActions.length) {
      const grouped = {};
      matchedActions.forEach(a => { (grouped[a.section || 'Actions'] = grouped[a.section || 'Actions'] || []).push(a); });
      Object.entries(grouped).forEach(([section, items]) => {
        html += `<div class="cmdk-section">${escapeHtml(section)}</div>`;
        items.forEach(a => {
          html += `<div class="cmdk-item" data-action="${escapeHtml(a.label)}">
            ${a.icon || ''}
            <span>${escapeHtml(a.label)}</span>
          </div>`;
        });
      });
    }
    if (matchedLeads.length) {
      html += `<div class="cmdk-section">Leads</div>`;
      matchedLeads.forEach(lead => {
        const tag = lead.urgency_score >= 4 ? '<span class="cmdk-meta" style="color:var(--error-text)">urgent</span>' : `<span class="cmdk-meta">${escapeHtml(lead.category || '')}</span>`;
        html += `<div class="cmdk-item" data-lead-id="${lead.id}">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg>
          <span>${escapeHtml(lead.sender_name || lead.sender_email)} — ${escapeHtml((lead.subject || '(no subject)').slice(0, 60))}</span>
          ${tag}
        </div>`;
      });
    }
    if (!html) html = '<div class="cmdk-empty">No results</div>';
    results.innerHTML = html;

    results.querySelectorAll('.cmdk-item').forEach((el, i) => {
      el.addEventListener('mouseenter', () => { cmdkActiveIdx = i; renderCmdkActive(); });
      el.addEventListener('click', () => {
        const actionLabel = el.dataset.action;
        const leadId = el.dataset.leadId;
        if (actionLabel) {
          const action = cmdkActions.find(a => a.label === actionLabel);
          closeCmdk();
          action?.run();
        } else if (leadId) {
          closeCmdk();
          // If on review page, fire a custom event; else navigate.
          if (location.pathname === '/review' && typeof window.__lrSelectLead === 'function') {
            window.__lrSelectLead(parseInt(leadId, 10));
          } else {
            location.href = `/review?lead=${leadId}`;
          }
        }
      });
    });
    renderCmdkActive();
  }

  // ---------------------- Keyboard shortcuts overlay ----------------------
  function showShortcuts() {
    if (document.getElementById('shortcutsOverlay')) return;
    const ov = document.createElement('div');
    ov.id = 'shortcutsOverlay';
    ov.className = 'shortcuts-overlay';
    ov.innerHTML = `
      <div class="shortcuts-panel">
        <h2>Keyboard shortcuts</h2>
        <div class="shortcuts-grid">
          <div class="shortcut-row"><span>Command palette</span><span class="shortcut-keys"><span class="shortcut-key">${navigator.platform.includes('Mac') ? '⌘' : 'Ctrl'}</span><span class="shortcut-key">K</span></span></div>
          <div class="shortcut-row"><span>Go to Queue</span><span class="shortcut-keys"><span class="shortcut-key">g</span><span class="shortcut-key">q</span></span></div>
          <div class="shortcut-row"><span>Go to Pipeline</span><span class="shortcut-keys"><span class="shortcut-key">g</span><span class="shortcut-key">p</span></span></div>
          <div class="shortcut-row"><span>Go to Analytics</span><span class="shortcut-keys"><span class="shortcut-key">g</span><span class="shortcut-key">a</span></span></div>
          <div class="shortcut-row"><span>Go to Settings</span><span class="shortcut-keys"><span class="shortcut-key">g</span><span class="shortcut-key">s</span></span></div>
          <div class="shortcut-row"><span>Next / previous lead</span><span class="shortcut-keys"><span class="shortcut-key">j</span><span class="shortcut-key">k</span></span></div>
          <div class="shortcut-row"><span>Send draft reply</span><span class="shortcut-keys"><span class="shortcut-key">⌘</span><span class="shortcut-key">↵</span></span></div>
          <div class="shortcut-row"><span>Mark Won</span><span class="shortcut-keys"><span class="shortcut-key">w</span></span></div>
          <div class="shortcut-row"><span>Mark Lost</span><span class="shortcut-keys"><span class="shortcut-key">l</span></span></div>
          <div class="shortcut-row"><span>Star / unstar</span><span class="shortcut-keys"><span class="shortcut-key">s</span></span></div>
          <div class="shortcut-row"><span>Refresh</span><span class="shortcut-keys"><span class="shortcut-key">r</span></span></div>
          <div class="shortcut-row"><span>Show this overlay</span><span class="shortcut-keys"><span class="shortcut-key">?</span></span></div>
        </div>
        <button class="ghost-btn shortcuts-close">Close</button>
      </div>
    `;
    document.body.appendChild(ov);
    const close = () => ov.remove();
    ov.addEventListener('click', (e) => { if (e.target === ov) close(); });
    ov.querySelector('.shortcuts-close').addEventListener('click', close);
    const handler = (e) => {
      if (e.key === 'Escape') { close(); document.removeEventListener('keydown', handler, true); }
    };
    document.addEventListener('keydown', handler, true);
  }

  // ---------------------- Global key bindings ----------------------
  let lastKey = '';
  let lastKeyTime = 0;

  function isTypingTarget(target) {
    if (!target) return false;
    const tag = target.tagName;
    return tag === 'INPUT' || tag === 'TEXTAREA' || target.isContentEditable;
  }

  document.addEventListener('keydown', (e) => {
    // Cmd+K / Ctrl+K
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      if (cmdkOverlay) closeCmdk();
      else openCmdk();
      return;
    }

    if (isTypingTarget(e.target)) return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;

    // Single-key shortcuts
    if (e.key === '?') { e.preventDefault(); showShortcuts(); return; }

    // Two-key g* navigation
    const now = Date.now();
    if (lastKey === 'g' && now - lastKeyTime < 1200) {
      lastKey = '';
      const next = e.key.toLowerCase();
      if (next === 'q') { e.preventDefault(); location.href = '/review'; return; }
      if (next === 'p') { e.preventDefault(); location.href = '/pipeline'; return; }
      if (next === 'a') { e.preventDefault(); location.href = '/analytics'; return; }
      if (next === 's') { e.preventDefault(); location.href = '/settings'; return; }
      if (next === 't') { e.preventDefault(); location.href = '/templates'; return; }
    }
    if (e.key.toLowerCase() === 'g') {
      lastKey = 'g';
      lastKeyTime = now;
    }
  });

  // ---------------------- Public API ----------------------
  window.LR = {
    toast,
    success: (msg, opts) => toast(msg, 'success', opts),
    error: (msg, opts) => toast(msg, 'error', opts),
    warning: (msg, opts) => toast(msg, 'warning', opts),
    info: (msg, opts) => toast(msg, 'info', opts),
    openCmdk,
    closeCmdk,
    registerActions,
    showShortcuts,
    escapeHtml,
    formatCurrency: (v) => v == null ? '—' : '$' + Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 }),
    timeSince: (iso) => {
      if (!iso) return '';
      const ms = Date.now() - new Date(iso).getTime();
      const m = Math.floor(ms / 60000);
      if (m < 1) return 'just now';
      if (m < 60) return `${m}m`;
      const h = Math.floor(m / 60);
      if (h < 24) return `${h}h`;
      const d = Math.floor(h / 24);
      if (d < 30) return `${d}d`;
      return new Date(iso).toLocaleDateString();
    },
    slaTier: (iso, status) => {
      if (status === 'sent') return 'done';
      const ms = Date.now() - new Date(iso).getTime();
      const m = ms / 60000;
      if (m < 5) return 'fresh';
      if (m < 60) return 'warm';
      return 'cold';
    },
  };
})();
