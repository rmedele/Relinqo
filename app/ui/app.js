const leadListEl = document.getElementById('leadList');
const threadListEl = document.getElementById('threadList');
const leadCountEl = document.getElementById('leadCount');
const refreshBtn = document.getElementById('refreshBtn');
const sendBtn = document.getElementById('sendBtn');
const markDraftBtn = document.getElementById('markDraftBtn');
const deleteBtn = document.getElementById('deleteBtn');
const saveReplyBtn = document.getElementById('saveReplyBtn');
const cancelSendBtn = document.getElementById('cancelSendBtn');
const replyEditorEl = document.getElementById('detailReplyEditor');
const activityListEl = document.getElementById('activityList');
const emptyStateEl = document.getElementById('emptyState');
const loadingStateEl = document.getElementById('loadingState');
const leadDetailEl = document.getElementById('leadDetail');
const sendStatusEl = document.getElementById('sendStatus');
const undoBar = document.getElementById('undoBar');
const undoCountdown = document.getElementById('undoCountdown');
const undoCancelBtn = document.getElementById('undoCancelBtn');
const threadInfoEl = document.getElementById('threadInfo');
const threadCountEl = document.getElementById('threadCount');
const showThreadBtn = document.getElementById('showThreadBtn');
const threadHistoryCard = document.getElementById('threadHistoryCard');
const threadHistoryEl = document.getElementById('threadHistory');
const autoPollToggle = document.getElementById('autoPollToggle');
const viewLeadsBtn = document.getElementById('viewLeads');
const viewThreadsBtn = document.getElementById('viewThreads');

// Filters
const searchInput = document.getElementById('searchInput');
const filterCategory = document.getElementById('filterCategory');
const filterStatus = document.getElementById('filterStatus');
const filterUrgency = document.getElementById('filterUrgency');
const leadMapEl = document.getElementById('leadMap');
const leadMapCountEl = document.getElementById('leadMapCount');
const backfillMapBtn = document.getElementById('backfillMapBtn');

let leads = [];
let threads = {};
let selectedLeadId = null;
let pollInterval = null;
let undoTimer = null;
let leadMap = null;
let leadMapMarkers = null;
let currentView = 'leads'; // 'leads' or 'threads'
let currentPage = 1;
let totalPages = 1;
const pageSize = 50;

function escapeHtml(value) {
  if (!value) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function urgencyLabel(score) {
  if (score >= 5) return 'High';
  if (score >= 3) return 'Medium';
  return 'Low';
}

function titleCase(value) {
  return (value || '\u2014')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function statusClass(value) {
  return `status-${(value || 'idle').replace(/_/g, '-')}`;
}

function formatDate(value) {
  if (!value) return '\u2014';
  return new Date(value).toLocaleString();
}

function showStatus(message, kind) {
  // Hybrid: keep the inline pill so existing flows still work, plus a toast.
  if (sendStatusEl) {
    sendStatusEl.textContent = message;
    sendStatusEl.className = `send-status ${kind}`;
    sendStatusEl.classList.remove('hidden');
  }
  if (window.LR && message) {
    const fn = window.LR[kind === 'error' ? 'error' : kind === 'success' ? 'success' : 'info'];
    if (fn) fn(message);
  }
}

function clearStatus() {
  if (!sendStatusEl) return;
  sendStatusEl.className = 'send-status hidden';
  sendStatusEl.textContent = '';
}

function slaBadge(lead) {
  if (!window.LR) return '';
  const tier = window.LR.slaTier(lead.created_at, lead.status);
  const label = tier === 'done' ? 'replied' : window.LR.timeSince(lead.created_at);
  return `<span class="sla-badge ${tier}"><span class="sla-dot"></span>${label}</span>`;
}

// --- Filtering ---

function getFilteredLeads() {
  const search = searchInput.value.toLowerCase().trim();
  const cat = filterCategory.value;
  const status = filterStatus.value;
  const urgency = filterUrgency.value;

  return leads.filter((lead) => {
    if (cat && lead.category !== cat) return false;
    if (status && lead.status !== status) return false;
    if (urgency) {
      if (urgency === 'high' && lead.urgency_score < 5) return false;
      if (urgency === 'medium' && (lead.urgency_score < 3 || lead.urgency_score > 4)) return false;
      if (urgency === 'low' && lead.urgency_score > 2) return false;
    }
    if (search) {
      const haystack = `${lead.sender_name || ''} ${lead.sender_email || ''} ${lead.subject || ''} ${lead.body || ''}`.toLowerCase();
      if (!haystack.includes(search)) return false;
    }
    return true;
  });
}

// --- Stats ---

async function loadStats() {
  try {
    const response = await fetch('/stats');
    if (!response.ok) return;
    const data = await response.json();
    document.getElementById('statTotal').textContent = data.total_leads;
    document.getElementById('statToday').textContent = data.today_leads;
    document.getElementById('statSent').textContent = data.sent_count;
    document.getElementById('statRate').textContent = `${data.response_rate}%`;
    document.getElementById('statAvgTime').textContent = data.avg_response_minutes != null
      ? `${data.avg_response_minutes}m`
      : '\u2014';
  } catch (e) { /* stats are non-critical */ }
}

// --- Undo Send ---

function startUndoCountdown(lead) {
  clearUndoCountdown();
  if (lead.status !== 'pending_send' || !lead.send_at) return;

  undoBar.classList.remove('hidden');
  cancelSendBtn.classList.remove('hidden');

  function tick() {
    const remaining = Math.max(0, Math.round((new Date(lead.send_at).getTime() - Date.now()) / 1000));
    undoCountdown.textContent = remaining;
    if (remaining <= 0) {
      clearUndoCountdown();
      loadLeads(); // refresh to get updated status
    }
  }
  tick();
  undoTimer = setInterval(tick, 1000);
}

function clearUndoCountdown() {
  if (undoTimer) clearInterval(undoTimer);
  undoTimer = null;
  undoBar.classList.add('hidden');
  cancelSendBtn.classList.add('hidden');
}

async function cancelAutoSend() {
  if (!selectedLeadId) return;
  try {
    const response = await fetch(`/leads/${selectedLeadId}/cancel-send`, { method: 'POST' });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Failed to cancel');
    showStatus('Auto-send cancelled', 'success');
    clearUndoCountdown();
    await loadLeads();
  } catch (error) {
    showStatus(error.message, 'error');
  }
}

// --- Thread View ---

async function loadThreads() {
  try {
    const response = await fetch('/threads');
    if (!response.ok) return;
    const data = await response.json();
    threads = data.threads;
    renderThreadList();
  } catch (e) { /* non-critical */ }
}

function renderThreadList() {
  threadListEl.innerHTML = '';
  const entries = Object.entries(threads);
  entries.sort((a, b) => {
    const aDate = a[1][0]?.created_at || '';
    const bDate = b[1][0]?.created_at || '';
    return bDate.localeCompare(aDate);
  });

  entries.forEach(([threadId, threadLeads]) => {
    const latest = threadLeads[0];
    const count = threadLeads.length;
    const button = document.createElement('button');
    button.className = `lead-item ${statusClass(latest.status)}-row`;
    button.innerHTML = `
      <div class="lead-item-top">
        <div>
          <strong>${escapeHtml(latest.sender_name) || 'Unknown sender'}</strong>
          <p class="muted">${escapeHtml(latest.sender_email)}</p>
        </div>
        <span class="badge thread-badge">${count} msg${count > 1 ? 's' : ''}</span>
      </div>
      <p class="lead-subject">${escapeHtml(latest.subject) || '(no subject)'}</p>
      <div class="lead-item-bottom">
        <span class="muted">${titleCase(latest.category)} \u2022 ${urgencyLabel(latest.urgency_score)} priority</span>
        <span class="muted">${formatDate(latest.created_at)}</span>
      </div>
    `;
    button.addEventListener('click', () => {
      selectedLeadId = latest.id;
      renderLeadDetail();
    });
    threadListEl.appendChild(button);
  });
}

async function showThreadHistory(lead) {
  if (!lead || !lead.thread_id) {
    threadInfoEl.classList.add('hidden');
    threadHistoryCard.classList.add('hidden');
    return;
  }

  // Find all leads with same thread_id from local data first
  const threadLeads = leads.filter((l) => l.thread_id === lead.thread_id);
  if (threadLeads.length <= 1) {
    threadInfoEl.classList.add('hidden');
    threadHistoryCard.classList.add('hidden');
    return;
  }

  threadInfoEl.classList.remove('hidden');
  threadCountEl.textContent = threadLeads.length;

  // Load full conversation from API
  try {
    const res = await fetch(`/threads/${lead.thread_id}`);
    if (!res.ok) throw new Error('Failed to load thread');
    const data = await res.json();
    renderConversationTimeline(data.messages, lead.id);
  } catch (e) {
    // Fallback to simple view
    const others = threadLeads.filter((l) => l.id !== lead.id).sort((a, b) =>
      new Date(a.created_at) - new Date(b.created_at)
    );
    threadHistoryEl.innerHTML = others.map((l) => `
      <div class="thread-message">
        <div class="thread-message-header">
          <strong>${escapeHtml(l.subject) || '(no subject)'}</strong>
          <span class="muted">${formatDate(l.created_at)}</span>
        </div>
        <span class="badge ${statusClass(l.status)}">${titleCase(l.status)}</span>
        <p class="thread-message-body">${escapeHtml((l.body || '').substring(0, 200))}${(l.body || '').length > 200 ? '...' : ''}</p>
      </div>
    `).join('');
  }
}

function renderConversationTimeline(messages, currentLeadId) {
  const timeline = [];

  messages.forEach((msg) => {
    // Incoming customer message
    timeline.push({
      type: 'incoming',
      sender: msg.sender_name || 'Customer',
      subject: msg.subject,
      body: msg.body,
      time: msg.created_at,
      status: msg.status,
      isCurrent: msg.id === currentLeadId,
    });

    // If a reply was sent, show it as outgoing
    if (msg.recommended_reply && (msg.status === 'sent' || msg.status === 'pending_send' || msg.status === 'drafted')) {
      const replySentActivity = msg.activities?.find(a => a.type === 'reply_sent' || a.type === 'auto_sent');
      timeline.push({
        type: 'outgoing',
        sender: 'You',
        subject: `Re: ${msg.subject || 'Your inquiry'}`,
        body: msg.recommended_reply,
        time: replySentActivity ? replySentActivity.created_at : msg.created_at,
        status: msg.status,
        isCurrent: false,
      });
    }
  });

  // Sort by time
  timeline.sort((a, b) => new Date(a.time) - new Date(b.time));

  threadHistoryEl.innerHTML = `<div class="conversation-timeline">${timeline.map((item) => {
    const statusLabel = item.type === 'outgoing'
      ? (item.status === 'sent' ? 'sent' : item.status === 'pending_send' ? 'pending' : 'drafted')
      : '';

    const highlight = item.isCurrent ? 'border-color: var(--border-active);' : '';

    return `<div class="convo-msg ${item.type}" style="${highlight}">
      <div class="convo-header">
        <span class="convo-sender">${escapeHtml(item.sender)}</span>
        <span class="convo-time">${formatDate(item.time)}</span>
      </div>
      ${item.subject ? `<div class="convo-subject">${escapeHtml(item.subject)}</div>` : ''}
      <div class="convo-body">${escapeHtml((item.body || '').substring(0, 300))}${(item.body || '').length > 300 ? '...' : ''}</div>
      ${statusLabel ? `<span class="convo-status ${statusLabel}">${titleCase(statusLabel)}</span>` : ''}
    </div>`;
  }).join('')}</div>`;
}

// --- Button State ---

function setSendButtonState(lead) {
  if (!lead) {
    sendBtn.disabled = true;
    markDraftBtn.disabled = true;
    deleteBtn.disabled = true;
    saveReplyBtn.disabled = true;
    cancelSendBtn.classList.add('hidden');
    sendBtn.textContent = 'Send Draft Reply';
    return;
  }

  deleteBtn.disabled = false;
  saveReplyBtn.disabled = false;
  markDraftBtn.disabled = false;

  if (lead.status === 'pending_send') {
    sendBtn.disabled = true;
    sendBtn.textContent = 'Sending Soon...';
    cancelSendBtn.classList.remove('hidden');
    return;
  }

  cancelSendBtn.classList.add('hidden');

  if (lead.category === 'spam') {
    sendBtn.disabled = true;
    sendBtn.textContent = 'Spam Lead';
    return;
  }

  if (lead.status === 'sent') {
    sendBtn.disabled = true;
    sendBtn.textContent = 'Reply Sent';
    return;
  }

  sendBtn.disabled = false;
  sendBtn.textContent = lead.status === 'send_failed' ? 'Retry Send' : 'Send Draft Reply';
}

// --- Render ---

function renderLeadList() {
  const filtered = getFilteredLeads();
  leadCountEl.textContent = `${filtered.length} lead${filtered.length === 1 ? '' : 's'}${filtered.length !== leads.length ? ` / ${leads.length} total` : ''}`;
  leadListEl.innerHTML = '';

  filtered.forEach((lead) => {
    const button = document.createElement('button');
    button.className = `lead-item ${statusClass(lead.status)}-row ${lead.id === selectedLeadId ? 'active' : ''}`;
    const pendingIcon = lead.status === 'pending_send' ? '<span class="pending-icon" title="Auto-sending soon">&#9200;</span> ' : '';
    const starHtml = `<button class="star-toggle ${lead.starred ? 'starred' : ''}" data-star-id="${lead.id}" title="${lead.starred ? 'Unstar' : 'Star'}">${lead.starred ? '\u2605' : '\u2606'}</button>`;
    const valueHtml = lead.deal_value ? `<span class="muted" style="color:var(--success-text); font-weight:600;">${window.LR ? window.LR.formatCurrency(lead.deal_value) : ('$' + lead.deal_value)}</span>` : '';
    button.innerHTML = `
      <div class="lead-item-top">
        <div>
          <strong>${pendingIcon}${escapeHtml(lead.sender_name) || 'Unknown sender'}</strong>
          <p class="muted">${escapeHtml(lead.sender_email)}</p>
        </div>
        <div style="display:flex; gap:6px; align-items:center;">
          ${slaBadge(lead)}
          ${starHtml}
          <span class="badge ${statusClass(lead.status)}">${titleCase(lead.status)}</span>
        </div>
      </div>
      <p class="lead-subject">${escapeHtml(lead.subject) || '(no subject)'}</p>
      <div class="lead-item-bottom">
        <span class="muted">${titleCase(lead.category)} \u2022 ${urgencyLabel(lead.urgency_score)} priority</span>
        <div style="display:flex; gap:8px; align-items:center;">
          ${valueHtml}
          <span class="muted">${titleCase(lead.source)}</span>
        </div>
      </div>
    `;
    button.addEventListener('click', (e) => {
      if (e.target.closest('.star-toggle')) {
        e.preventDefault();
        e.stopPropagation();
        toggleStar(lead.id);
        return;
      }
      selectLead(lead.id);
    });
    leadListEl.appendChild(button);
  });

  // Pagination controls
  if (totalPages > 1) {
    const pager = document.createElement('div');
    pager.className = 'pagination';
    pager.innerHTML = `
      <button ${currentPage <= 1 ? 'disabled' : ''} id="prevPage">&larr; Prev</button>
      <span>Page ${currentPage} / ${totalPages}</span>
      <button ${currentPage >= totalPages ? 'disabled' : ''} id="nextPage">Next &rarr;</button>
    `;
    leadListEl.appendChild(pager);
    document.getElementById('prevPage')?.addEventListener('click', () => { currentPage--; loadLeads(); });
    document.getElementById('nextPage')?.addEventListener('click', () => { currentPage++; loadLeads(); });
  }
}

function fill(id, value) {
  document.getElementById(id).textContent = value || '\u2014';
}

async function loadActivities(leadId) {
  const response = await fetch(`/leads/${leadId}/activities`);
  if (!response.ok) {
    activityListEl.innerHTML = '<p class="muted">Failed to load activity.</p>';
    return;
  }
  const activities = await response.json();
  if (!activities.length) {
    activityListEl.innerHTML = '<p class="muted">No activity yet.</p>';
    return;
  }
  activityListEl.innerHTML = activities.map((item) => `
    <div class="activity-item">
      <strong>${titleCase(item.activity_type)}</strong>
      <div>${item.message}</div>
      <time>${formatDate(item.created_at)}</time>
    </div>
  `).join('');
}

function renderLeadDetail() {
  const lead = leads.find((item) => item.id === selectedLeadId);
  if (!lead) {
    emptyStateEl.classList.remove('hidden');
    loadingStateEl.classList.add('hidden');
    leadDetailEl.classList.add('hidden');
    setSendButtonState(null);
    clearUndoCountdown();
    return;
  }

  emptyStateEl.classList.add('hidden');
  loadingStateEl.classList.add('hidden');
  leadDetailEl.classList.remove('hidden');
  clearStatus();

  fill('detailCategory', titleCase(lead.category));
  fill('detailSubject', lead.subject || '(no subject)');
  fill('detailMeta', `${lead.sender_email} \u2022 ${titleCase(lead.status)}`);
  fill('detailName', lead.sender_name || '\u2014');
  fill('detailEmail', lead.sender_email);
  fill('detailPhone', lead.phone || '\u2014');
  fill('detailLocation', lead.location || '\u2014');
  fill('detailUrgency', urgencyLabel(lead.urgency_score));
  fill('detailConfidence', `${Math.round((lead.confidence || 0) * 100)}%`);
  fill('detailStatus', titleCase(lead.status));
  fill('detailAlert', lead.owner_alert_needed ? 'Yes' : 'No');
  fill('detailCreated', formatDate(lead.created_at));
  fill('detailSummary', lead.summary || '\u2014');
  fill('detailBody', lead.body || '\u2014');
  fill('detailPriority', urgencyLabel(lead.urgency_score));
  fill('detailCategoryText', titleCase(lead.category));
  fill('detailSource', titleCase(lead.source));
  fill('detailNextStep', titleCase(lead.next_step));
  replyEditorEl.value = lead.recommended_reply || '';

  // Deal tracking
  const stageEl = document.getElementById('detailPipelineStage');
  if (stageEl) stageEl.value = lead.pipeline_stage || 'new';
  const dealValueEl = document.getElementById('detailDealValue');
  if (dealValueEl) dealValueEl.value = lead.deal_value ?? '';
  renderTags(lead.tags || '');
  const starBtn = document.getElementById('starToggleBtn');
  if (starBtn) {
    starBtn.classList.toggle('starred', !!lead.starred);
    starBtn.innerHTML = lead.starred ? '★ Starred' : '☆ Star';
  }
  // SLA timer in operator snapshot
  const slaEl = document.getElementById('detailSlaTimer');
  if (slaEl && window.LR) {
    const tier = window.LR.slaTier(lead.created_at, lead.status);
    const label = tier === 'done' ? 'replied' : (window.LR.timeSince(lead.created_at) || '0m');
    slaEl.innerHTML = `<span class="sla-badge ${tier}"><span class="sla-dot"></span>${label}</span>`;
  }

  const statusBadge = document.getElementById('detailStatusBadge');
  statusBadge.textContent = titleCase(lead.status);
  statusBadge.className = `status-chip ${statusClass(lead.status)}`;

  const deliveryStatusEl = document.getElementById('detailDeliveryStatus');
  if (lead.status === 'sent') {
    deliveryStatusEl.textContent = `Reply sent to ${lead.sender_email}.`;
    deliveryStatusEl.className = 'prewrap muted-strong delivery-ok';
  } else if (lead.status === 'send_failed') {
    deliveryStatusEl.textContent = 'Last send attempt failed. Review SMTP settings or retry send.';
    deliveryStatusEl.className = 'prewrap muted-strong delivery-fail';
  } else if (lead.status === 'pending_send') {
    deliveryStatusEl.textContent = 'Auto-reply queued. Cancel within the countdown window.';
    deliveryStatusEl.className = 'prewrap muted-strong delivery-pending';
  } else {
    deliveryStatusEl.textContent = 'Not sent yet.';
    deliveryStatusEl.className = 'prewrap muted-strong delivery-pending';
  }

  setSendButtonState(lead);

  // Undo send countdown
  if (lead.status === 'pending_send' && lead.send_at) {
    startUndoCountdown(lead);
  } else {
    clearUndoCountdown();
  }

  // Thread info
  showThreadHistory(lead);

  // Outcome
  const outcomeStatusEl = document.getElementById('outcomeStatus');
  const outcomeNotesEl = document.getElementById('outcomeNotes');
  outcomeNotesEl.value = lead.outcome_notes || '';
  if (lead.outcome) {
    outcomeStatusEl.textContent = `Outcome: ${titleCase(lead.outcome)}` + (lead.outcome_at ? ` (${formatDate(lead.outcome_at)})` : '');
  } else {
    outcomeStatusEl.textContent = 'No outcome set yet';
  }
  document.querySelectorAll('.outcome-btn').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.outcome === lead.outcome);
  });

  updateQuickActions(lead);
  loadActivities(lead.id);
  loadPhotos(lead.id);
  loadNotes(lead.id);
}

async function loadPhotos(leadId) {
  const photoCard = document.getElementById('photoCard');
  const gallery = document.getElementById('photoGallery');
  const badge = document.getElementById('photoCountBadge');
  try {
    const res = await fetch(`/leads/${leadId}/photos`);
    if (!res.ok) { photoCard.classList.add('hidden'); return; }
    const photos = await res.json();
    if (!photos.length) { photoCard.classList.add('hidden'); return; }
    photoCard.classList.remove('hidden');
    badge.textContent = photos.length;
    gallery.innerHTML = photos.map((p) => `
      <div class="photo-thumb" onclick="openLightbox('/leads/${leadId}/photos/${p.id}', ${JSON.stringify(escapeHtml(p.ai_analysis || ''))})">
        <img src="/leads/${leadId}/photos/${p.id}" alt="${escapeHtml(p.filename)}" loading="lazy" />
        <span class="photo-name">${escapeHtml(p.filename)}</span>
      </div>
    `).join('');
  } catch { photoCard.classList.add('hidden'); }
}

function openLightbox(src, analysis) {
  document.getElementById('lightboxImage').src = src;
  document.getElementById('lightboxAnalysis').textContent = analysis || '';
  document.getElementById('photoLightbox').classList.remove('hidden');
}

function closeLightbox() {
  document.getElementById('photoLightbox').classList.add('hidden');
  document.getElementById('lightboxImage').src = '';
}

function scrollToLeadDetail() {
  requestAnimationFrame(() => {
    leadDetailEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
}

function selectLead(id, options = {}) {
  selectedLeadId = id;
  renderLeadList();
  renderLeadDetail();
  // On mobile (stacked layout), scroll the detail panel into view so the user
  // doesn't have to scroll past the entire lead list after tapping.
  if (options.scrollToDetail || window.matchMedia('(max-width: 1180px)').matches) {
    scrollToLeadDetail();
  }
}

async function selectLeadById(id) {
  if (!leads.some((lead) => lead.id === id)) {
    const response = await fetch(`/leads/${id}`);
    if (response.ok) {
      const lead = await response.json();
      leads = [lead, ...leads.filter((item) => item.id !== lead.id)];
    }
  }
  selectLead(id, { scrollToDetail: true });
}

// --- Data Loading ---

async function loadLeads() {
  if (!leads.length) {
    loadingStateEl.classList.remove('hidden');
    emptyStateEl.classList.add('hidden');
    leadDetailEl.classList.add('hidden');
  }

  const showSpam = filterCategory.value === 'spam' || filterStatus.value === 'spam';
  const response = await fetch(`/leads?page=${currentPage}&page_size=${pageSize}${showSpam ? '&include_spam=true' : ''}`);
  if (!response.ok) {
    loadingStateEl.classList.add('hidden');
    throw new Error('Failed to load leads');
  }
  const data = await response.json();
  leads = data.items;
  totalPages = data.pages;
  if (!selectedLeadId && leads.length) {
    selectedLeadId = leads[0].id;
  }
  if (selectedLeadId && !leads.some((lead) => lead.id === selectedLeadId)) {
    selectedLeadId = leads[0]?.id ?? null;
  }
  renderLeadList();
  renderLeadDetail();
  renderPriorityLeads();
  loadStats();
  loadLeadMap();
}

// --- Lead Map ---

function mapMarkerClass(item) {
  if (item.outcome === 'won') return 'won';
  if (item.urgency_score >= 4) return 'urgent';
  if (item.status === 'sent') return 'sent';
  return 'active';
}

function initLeadMap() {
  if (!leadMapEl || leadMap || !window.L) return;
  leadMap = L.map(leadMapEl, { scrollWheelZoom: false });
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap contributors',
  }).addTo(leadMap);
  leadMapMarkers = L.layerGroup().addTo(leadMap);
  leadMap.setView([53.5461, -113.4938], 10);
}

async function loadLeadMap() {
  if (!leadMapEl || !window.L) return;
  initLeadMap();
  try {
    const response = await fetch('/api/lead-map');
    if (!response.ok) return;
    const data = await response.json();
    leadMapMarkers.clearLayers();
    const bounds = [];
    data.items.forEach((item) => {
      const icon = L.divIcon({
        className: `lead-map-marker ${mapMarkerClass(item)}`,
        iconSize: [18, 18],
        iconAnchor: [9, 9],
      });
      const marker = L.marker([item.lat, item.lng], { icon }).addTo(leadMapMarkers);
      marker.bindPopup(`
        <strong>${escapeHtml(item.subject) || 'Lead'}</strong><br>
        <span>${escapeHtml(item.location) || escapeHtml(item.sender_email) || ''}</span><br>
        <button type="button" class="map-popup-btn" data-lead-id="${item.id}">Open inquiry</button>
      `);
      marker.on('popupopen', () => {
        document.querySelector(`.map-popup-btn[data-lead-id="${item.id}"]`)?.addEventListener('click', () => {
          selectLeadById(item.id);
        });
      });
      bounds.push([item.lat, item.lng]);
    });
    if (leadMapCountEl) {
      leadMapCountEl.textContent = `${data.items.length} mapped lead${data.items.length === 1 ? '' : 's'}`;
    }
    if (bounds.length) {
      leadMap.fitBounds(bounds, { padding: [28, 28], maxZoom: 13 });
    }
  } catch (e) {
    if (leadMapCountEl) leadMapCountEl.textContent = 'Map unavailable';
  }
}

async function backfillLeadMap() {
  if (!backfillMapBtn) return;
  backfillMapBtn.disabled = true;
  backfillMapBtn.textContent = 'Mapping...';
  try {
    const response = await fetch('/api/lead-map/backfill', { method: 'POST' });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Failed to map past leads');
    showStatus(`Mapped ${data.updated} past lead${data.updated === 1 ? '' : 's'}`, 'success');
    await loadLeadMap();
  } catch (error) {
    showStatus(error.message || 'Failed to map past leads', 'error');
  } finally {
    backfillMapBtn.disabled = false;
    backfillMapBtn.textContent = 'Map Past Leads';
  }
}

// --- Actions ---

async function updateLead(fields, successMessage) {
  if (!selectedLeadId) return;
  const response = await fetch(`/leads/${selectedLeadId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(fields),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || 'Failed to update lead');
  }
  showStatus(successMessage, 'success');
  await loadLeads();
}

async function sendDraftReply() {
  if (!selectedLeadId) return;
  clearStatus();
  sendBtn.disabled = true;
  sendBtn.textContent = 'Sending\u2026';

  try {
    await updateLead({ recommended_reply: replyEditorEl.value }, 'Draft saved');
    const response = await fetch(`/leads/${selectedLeadId}/review/send`, { method: 'POST' });
    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.detail || 'Failed to send drafted reply');
    }

    showStatus(data.message || 'Reply processed', data.sent ? 'success' : 'error');
    await loadLeads();
  } catch (error) {
    showStatus(error.message || 'Unexpected send failure', 'error');
    const lead = leads.find((item) => item.id === selectedLeadId);
    setSendButtonState(lead);
  }
}

async function saveReply() {
  try {
    await updateLead({ recommended_reply: replyEditorEl.value }, 'Reply draft saved');
  } catch (error) {
    showStatus(error.message || 'Failed to save reply', 'error');
  }
}

async function markDrafted() {
  try {
    await updateLead({ status: 'drafted' }, 'Lead marked drafted');
  } catch (error) {
    showStatus(error.message || 'Failed to update lead', 'error');
  }
}

async function deleteLead() {
  if (!selectedLeadId) return;
  if (!confirm('Delete this lead?')) return;

  try {
    const response = await fetch(`/leads/${selectedLeadId}`, { method: 'DELETE' });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || 'Failed to delete lead');
    }
    showStatus(`Deleted lead ${data.deleted_id}`, 'success');
    selectedLeadId = null;
    await loadLeads();
  } catch (error) {
    showStatus(error.message || 'Failed to delete lead', 'error');
  }
}

// --- Outcome ---

document.addEventListener('click', async (e) => {
  const btn = e.target.closest('.outcome-btn');
  if (!btn || !selectedLeadId) return;
  const outcome = btn.dataset.outcome;
  const notes = document.getElementById('outcomeNotes').value.trim();
  try {
    const res = await fetch(`/leads/${selectedLeadId}/outcome`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ outcome, outcome_notes: notes || null }),
    });
    if (!res.ok) { const d = await res.json(); throw new Error(d.detail); }
    showStatus(`Outcome set to "${outcome}"`, 'success');
    await loadLeads();
  } catch (err) {
    showStatus(err.message, 'error');
  }
});

// --- View Toggle ---

function setView(view) {
  currentView = view;
  if (view === 'leads') {
    viewLeadsBtn.classList.add('active');
    viewThreadsBtn.classList.remove('active');
    leadListEl.classList.remove('hidden');
    threadListEl.classList.add('hidden');
  } else {
    viewThreadsBtn.classList.add('active');
    viewLeadsBtn.classList.remove('active');
    threadListEl.classList.remove('hidden');
    leadListEl.classList.add('hidden');
    loadThreads();
  }
}

// --- Auto-Polling ---

function startPolling() {
  stopPolling();
  pollInterval = setInterval(() => {
    loadLeads().catch(() => {});
  }, 10000);
}

function stopPolling() {
  if (pollInterval) clearInterval(pollInterval);
  pollInterval = null;
}

// --- Event Listeners ---

refreshBtn.addEventListener('click', () => {
  loadLeads();
  if (currentView === 'threads') loadThreads();
});
sendBtn.addEventListener('click', sendDraftReply);
markDraftBtn.addEventListener('click', markDrafted);
deleteBtn.addEventListener('click', deleteLead);
saveReplyBtn.addEventListener('click', saveReply);
cancelSendBtn.addEventListener('click', cancelAutoSend);
undoCancelBtn.addEventListener('click', cancelAutoSend);
viewLeadsBtn.addEventListener('click', () => setView('leads'));
viewThreadsBtn.addEventListener('click', () => setView('threads'));

showThreadBtn.addEventListener('click', () => {
  const isHidden = threadHistoryCard.classList.toggle('hidden');
  showThreadBtn.textContent = isHidden ? 'View Thread' : 'Hide Thread';
});

autoPollToggle.addEventListener('change', () => {
  if (autoPollToggle.checked) {
    startPolling();
  } else {
    stopPolling();
  }
});

// Filters — re-render on change
searchInput.addEventListener('input', renderLeadList);
filterCategory.addEventListener('change', () => { currentPage = 1; loadLeads(); });
filterStatus.addEventListener('change', () => { currentPage = 1; loadLeads(); });
filterUrgency.addEventListener('change', renderLeadList);
backfillMapBtn?.addEventListener('click', backfillLeadMap);

// --- Auth ---

async function checkAuth() {
  try {
    const res = await fetch('/auth/me');
    if (!res.ok) { window.location.href = '/login'; return; }
    const data = await res.json();
    const orgNameEl = document.getElementById('orgName');
    if (orgNameEl) orgNameEl.textContent = data.org.name;
    window.__lrOrgName = data.org?.name || '';
  } catch {
    window.location.href = '/login';
  }
}

document.getElementById('logoutBtn')?.addEventListener('click', async () => {
  await fetch('/auth/logout', { method: 'POST' });
  window.location.href = '/login';
});

// Intercept fetch to handle 401 redirects globally
const _origFetch = window.fetch;
window.fetch = async function(...args) {
  const res = await _origFetch.apply(this, args);
  if (res.status === 401 && !String(args[0]).includes('/auth/')) {
    window.location.href = '/login';
  }
  return res;
};

// --- Charts ---

let chartsVisible = true;

document.getElementById('toggleChartsBtn')?.addEventListener('click', () => {
  chartsVisible = !chartsVisible;
  document.getElementById('chartsGrid').classList.toggle('hidden', !chartsVisible);
  document.getElementById('toggleChartsBtn').textContent = chartsVisible ? 'Hide Charts' : 'Show Charts';
});

function renderDailyChart(daily) {
  const container = document.getElementById('dailyChart');
  if (!container || !daily.length) return;

  const maxVal = Math.max(1, ...daily.map(d => d.total));
  // Show every Nth label to avoid crowding
  const labelEvery = daily.length > 14 ? 7 : daily.length > 7 ? 3 : 1;

  container.innerHTML = daily.map((d, i) => {
    const totalH = Math.round((d.total / maxVal) * 100);
    const sentH = Math.round((d.sent / maxVal) * 100);
    const spamH = Math.round((d.spam / maxVal) * 100);
    const showLabel = i % labelEvery === 0;
    const dateLabel = d.date.slice(5); // MM-DD

    return `<div class="bar-group" title="${d.date}: ${d.total} leads, ${d.sent} sent, ${d.spam} spam">
      <div class="bar-stack">
        <div class="bar bar-total" style="height:${totalH}%"></div>
        <div class="bar bar-sent" style="height:${sentH}%"></div>
        ${spamH > 0 ? `<div class="bar bar-spam" style="height:${spamH}%"></div>` : ''}
      </div>
      <span class="bar-label">${showLabel ? dateLabel : ''}</span>
    </div>`;
  }).join('');
}

function renderHorizChart(containerId, data, colorFn) {
  const container = document.getElementById(containerId);
  if (!container) return;

  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
  const maxVal = Math.max(1, ...entries.map(e => e[1]));

  container.innerHTML = entries.map(([label, count], i) => {
    const pct = Math.round((count / maxVal) * 100);
    const color = colorFn ? colorFn(label, i) : 'var(--accent-blue)';
    const displayLabel = label.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    return `<div class="horiz-bar-row">
      <span class="horiz-label">${displayLabel}</span>
      <div class="horiz-track"><div class="horiz-fill" style="width:${pct}%; background:${color};"></div></div>
      <span class="horiz-count">${count}</span>
    </div>`;
  }).join('');
}

function renderFunnelChart(funnel) {
  const container = document.getElementById('funnelChart');
  if (!container) return;

  const steps = [
    { label: 'Total Leads', value: funnel.total, color: 'var(--accent-blue)' },
    { label: 'Replied', value: funnel.replied, color: 'var(--accent-lilac)' },
    { label: 'Won', value: funnel.won, color: 'var(--success-text)' },
    { label: 'Lost', value: funnel.lost, color: 'var(--error-text)' },
    { label: 'No Response', value: funnel.no_response, color: 'var(--accent-orange)' },
    { label: 'Awaiting Outcome', value: funnel.pending, color: 'var(--muted)' },
  ];

  const maxVal = Math.max(1, funnel.total);
  container.innerHTML = steps.map(s => {
    const pct = Math.round((s.value / maxVal) * 100);
    return `<div class="funnel-row">
      <span class="funnel-label">${s.label}</span>
      <div class="funnel-track"><div class="funnel-fill" style="width:${pct}%; background:${s.color};"></div></div>
      <span class="funnel-count">${s.value}</span>
    </div>`;
  }).join('');
}

const categoryColors = {
  urgent_request: 'var(--error-text)',
  quote_request: 'var(--accent-orange)',
  general_inquiry: 'var(--accent-blue)',
  existing_customer: 'var(--accent-lilac)',
};

const sourceColors = ['var(--accent-blue)', 'var(--accent-orange)', 'var(--accent-lilac)', 'var(--success-text)', 'var(--error-text)', 'var(--muted)'];

async function loadCharts() {
  try {
    const res = await fetch('/stats/charts');
    if (!res.ok) return;
    const data = await res.json();
    renderDailyChart(data.daily);
    renderHorizChart('categoryChart', data.by_category, (label) => categoryColors[label] || 'var(--accent-blue)');
    renderHorizChart('sourceChart', data.by_source, (_, i) => sourceColors[i % sourceColors.length]);
    renderFunnelChart(data.funnel);
  } catch (e) { /* charts are non-critical */ }
}

// --- Priority Leads ---

function renderPriorityLeads() {
  const section = document.getElementById('prioritySection');
  const container = document.getElementById('priorityCards');
  if (!section || !container) return;

  const now = new Date();
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());

  const priority = leads.filter((lead) => {
    // Urgent leads not yet sent
    if (lead.urgency_score >= 4 && lead.status !== 'sent' && lead.category !== 'spam') return true;
    // New leads from today
    if (lead.status === 'drafted' && new Date(lead.created_at) >= todayStart) return true;
    // Failed sends
    if (lead.status === 'send_failed') return true;
    return false;
  }).slice(0, 6);

  if (!priority.length) {
    section.classList.add('hidden');
    return;
  }
  section.classList.remove('hidden');

  container.innerHTML = priority.map((lead) => {
    let tagClass = 'priority-tag-new';
    let tagText = 'New';
    let cardClass = 'priority-new';
    if (lead.urgency_score >= 4) {
      tagClass = 'priority-tag-urgent';
      tagText = 'Urgent';
      cardClass = 'priority-urgent';
    } else if (lead.status === 'send_failed') {
      tagClass = 'priority-tag-pending';
      tagText = 'Failed';
      cardClass = 'priority-pending';
    } else if (lead.status === 'pending_send') {
      tagClass = 'priority-tag-pending';
      tagText = 'Sending';
      cardClass = 'priority-pending';
    }

    const age = Math.round((now - new Date(lead.created_at)) / 60000);
    const ageText = age < 60 ? `${age}m ago` : age < 1440 ? `${Math.round(age / 60)}h ago` : `${Math.round(age / 1440)}d ago`;

    return `<div class="priority-card ${cardClass}" data-lead-id="${lead.id}">
      <div class="priority-card-top">
        <strong>${escapeHtml(lead.sender_name) || 'Unknown'}</strong>
        <span class="priority-tag ${tagClass}">${tagText}</span>
      </div>
      <p class="priority-card-subject">${escapeHtml(lead.subject) || '(no subject)'}</p>
      <div class="priority-card-meta">
        <span>${titleCase(lead.category)}</span>
        <span>${ageText}</span>
      </div>
    </div>`;
  }).join('');

  container.querySelectorAll('.priority-card').forEach((card) => {
    card.addEventListener('click', () => {
      const id = parseInt(card.dataset.leadId);
      selectLead(id);
    });
  });
}

document.getElementById('dismissPriority')?.addEventListener('click', () => {
  document.getElementById('prioritySection').classList.add('hidden');
});


// --- Quick Actions ---

function updateQuickActions(lead) {
  const callBtn = document.getElementById('callCustomerBtn');
  const emailBtn = document.getElementById('emailCustomerBtn');
  if (!callBtn || !emailBtn) return;

  if (lead && lead.phone) {
    callBtn.href = `tel:${lead.phone.replace(/[^\d+]/g, '')}`;
    callBtn.style.display = 'inline-flex';
  } else {
    callBtn.style.display = 'none';
  }

  if (lead && lead.sender_email) {
    emailBtn.href = `mailto:${lead.sender_email}`;
    emailBtn.style.display = 'inline-flex';
  } else {
    emailBtn.style.display = 'none';
  }
}

function insertTemplate(type) {
  const lead = leads.find((item) => item.id === selectedLeadId);
  if (!lead) return;

  const name = lead.sender_name ? lead.sender_name.split(' ')[0] : 'there';
  const templates = {
    thanks: `Hi ${name},\n\nThank you for reaching out! We received your message and will get back to you shortly with more details.\n\nBest regards`,
    schedule: `Hi ${name},\n\nThanks for contacting us! I'd love to set up a time to take care of this for you. What days and times work best this week?\n\nLooking forward to hearing from you.`,
    quote: `Hi ${name},\n\nThank you for your inquiry. Based on your description, I'd like to provide you with a quote.\n\nService: \nEstimated cost: $\nTimeline: \n\nPlease let me know if you have any questions or would like to proceed.`,
  };

  if (templates[type]) {
    replyEditorEl.value = templates[type];
    replyEditorEl.focus();
  }
}

function backToList() {
  selectedLeadId = null;
  renderLeadDetail();
  const listPanel = document.querySelector('.list-panel');
  if (listPanel) listPanel.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// Make functions available globally for onclick handlers
window.insertTemplate = insertTemplate;
window.backToList = backToList;


// --- Tags ---

function renderTags(tagsRaw) {
  const row = document.getElementById('tagRow');
  if (!row) return;
  const input = document.getElementById('tagInput');
  // Remove existing pills
  row.querySelectorAll('.tag-pill').forEach(p => p.remove());
  const tags = (tagsRaw || '').split(',').map(t => t.trim()).filter(Boolean);
  tags.forEach(t => {
    const pill = document.createElement('span');
    pill.className = 'tag-pill';
    pill.innerHTML = `${escapeHtml(t)}<button class="tag-x" type="button" aria-label="remove">×</button>`;
    pill.querySelector('.tag-x').addEventListener('click', () => removeTag(t));
    row.insertBefore(pill, input);
  });
}

async function addTag(tag) {
  if (!selectedLeadId || !tag) return;
  const lead = leads.find(l => l.id === selectedLeadId);
  if (!lead) return;
  const existing = (lead.tags || '').split(',').map(t => t.trim()).filter(Boolean);
  if (existing.includes(tag)) return;
  existing.push(tag);
  const newTags = existing.join(',');
  try {
    await fetch(`/leads/${selectedLeadId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tags: newTags }),
    });
    lead.tags = newTags;
    renderTags(newTags);
  } catch (e) { window.LR?.error('Failed to add tag'); }
}

async function removeTag(tag) {
  if (!selectedLeadId) return;
  const lead = leads.find(l => l.id === selectedLeadId);
  if (!lead) return;
  const next = (lead.tags || '').split(',').map(t => t.trim()).filter(t => t && t !== tag).join(',');
  try {
    await fetch(`/leads/${selectedLeadId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tags: next }),
    });
    lead.tags = next;
    renderTags(next);
  } catch (e) { window.LR?.error('Failed to remove tag'); }
}

// --- Star ---

async function toggleStar(leadId) {
  const target = leadId ?? selectedLeadId;
  if (!target) return;
  const lead = leads.find(l => l.id === target);
  if (!lead) return;
  const newStarred = !lead.starred;
  try {
    await fetch(`/leads/${target}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ starred: newStarred }),
    });
    lead.starred = newStarred;
    renderLeadList();
    if (target === selectedLeadId) renderLeadDetail();
  } catch (e) { window.LR?.error('Failed to update star'); }
}

// --- Pipeline stage + deal value ---

async function updatePipelineStage(stage) {
  if (!selectedLeadId) return;
  try {
    await fetch(`/leads/${selectedLeadId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pipeline_stage: stage }),
    });
    const lead = leads.find(l => l.id === selectedLeadId);
    if (lead) lead.pipeline_stage = stage;
    window.LR?.success(`Moved to ${titleCase(stage)}`);
    loadStats();
  } catch (e) { window.LR?.error('Failed to update stage'); }
}

async function saveDealValue() {
  if (!selectedLeadId) return;
  const input = document.getElementById('detailDealValue');
  const value = input.value === '' ? null : parseFloat(input.value);
  try {
    await fetch(`/leads/${selectedLeadId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ deal_value: value }),
    });
    const lead = leads.find(l => l.id === selectedLeadId);
    if (lead) lead.deal_value = value;
    renderLeadList();
    window.LR?.success(value != null ? `Deal value saved: $${value.toLocaleString()}` : 'Deal value cleared');
    loadStats();
  } catch (e) { window.LR?.error('Failed to save deal value'); }
}

// --- Internal notes ---

let noteCache = {};

async function loadNotes(leadId) {
  const list = document.getElementById('notesList');
  if (!list) return;
  try {
    const res = await fetch(`/leads/${leadId}/notes`);
    if (!res.ok) { list.innerHTML = ''; return; }
    const notes = await res.json();
    noteCache[leadId] = notes;
    if (!notes.length) {
      list.innerHTML = '<p class="muted" style="text-align:center; padding: 14px; font-size:0.82rem;">No notes yet — add the first one above.</p>';
      return;
    }
    list.innerHTML = notes.map(n => `
      <div class="note-item ${n.pinned ? 'pinned' : ''}" data-note-id="${n.id}">
        ${n.pinned ? '<span class="note-pin-indicator">📌</span>' : ''}
        <div class="note-item-head">
          <strong>${escapeHtml(n.author_name || 'Team')}</strong>
          <span>${formatDate(n.created_at)}</span>
        </div>
        <div class="note-item-body">${escapeHtml(n.body)}</div>
        <div class="note-item-actions">
          <button data-action="pin">${n.pinned ? 'Unpin' : 'Pin'}</button>
          <button data-action="delete">Delete</button>
        </div>
      </div>
    `).join('');

    list.querySelectorAll('.note-item').forEach(el => {
      const id = parseInt(el.dataset.noteId, 10);
      const note = notes.find(n => n.id === id);
      el.querySelector('[data-action="pin"]').addEventListener('click', () => togglePinNote(id, !note.pinned));
      el.querySelector('[data-action="delete"]').addEventListener('click', () => deleteNote(id));
    });
  } catch (e) {
    list.innerHTML = '';
  }
}

async function addNote() {
  if (!selectedLeadId) return;
  const body = document.getElementById('newNoteBody').value.trim();
  const pinned = document.getElementById('newNotePinned').checked;
  if (!body) { window.LR?.error('Note can\'t be empty'); return; }
  try {
    const res = await fetch(`/leads/${selectedLeadId}/notes`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ body, pinned }),
    });
    if (!res.ok) throw new Error('Failed to add note');
    document.getElementById('newNoteBody').value = '';
    document.getElementById('newNotePinned').checked = false;
    window.LR?.success('Note added');
    loadNotes(selectedLeadId);
  } catch (e) { window.LR?.error(e.message); }
}

async function togglePinNote(noteId, pinned) {
  if (!selectedLeadId) return;
  try {
    await fetch(`/leads/${selectedLeadId}/notes/${noteId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pinned }),
    });
    loadNotes(selectedLeadId);
  } catch (e) { window.LR?.error('Failed to update note'); }
}

async function deleteNote(noteId) {
  if (!selectedLeadId) return;
  if (!confirm('Delete this note?')) return;
  try {
    await fetch(`/leads/${selectedLeadId}/notes/${noteId}`, { method: 'DELETE' });
    loadNotes(selectedLeadId);
  } catch (e) { window.LR?.error('Failed to delete note'); }
}

// --- Templates dropdown ---

let templateCache = null;

async function loadTemplatesIfNeeded() {
  if (templateCache !== null) return templateCache;
  try {
    const res = await fetch('/api/templates');
    if (!res.ok) { templateCache = []; return []; }
    templateCache = await res.json();
  } catch { templateCache = []; }
  return templateCache;
}

function fillTemplateVars(body, lead) {
  const firstName = (lead.sender_name || '').split(' ')[0] || 'there';
  return body
    .replaceAll('{{name}}', firstName)
    .replaceAll('{{full_name}}', lead.sender_name || firstName)
    .replaceAll('{{phone}}', lead.phone || '')
    .replaceAll('{{email}}', lead.sender_email || '')
    .replaceAll('{{location}}', lead.location || '')
    .replaceAll('{{business}}', (window.__lrOrgName || 'our team'));
}

async function openTemplateMenu() {
  const menu = document.getElementById('templateMenu');
  const tpls = await loadTemplatesIfNeeded();
  if (!tpls.length) {
    menu.innerHTML = `<div class="template-menu-empty">
      No templates yet — <a href="/templates" style="color:var(--accent-cyan);">create some</a> to reuse here.
    </div>`;
  } else {
    menu.innerHTML = tpls.map(t => `
      <div class="template-menu-item" data-id="${t.id}">
        <div class="tmi-name">${escapeHtml(t.name)}</div>
        <div class="tmi-preview">${escapeHtml(t.body.replace(/\n/g, ' ').slice(0, 80))}</div>
      </div>
    `).join('');
    menu.querySelectorAll('.template-menu-item').forEach(item => {
      item.addEventListener('click', () => {
        const id = parseInt(item.dataset.id, 10);
        const tpl = tpls.find(t => t.id === id);
        const lead = leads.find(l => l.id === selectedLeadId);
        if (tpl && lead) {
          replyEditorEl.value = fillTemplateVars(tpl.body, lead);
          replyEditorEl.focus();
          fetch(`/api/templates/${id}/use`, { method: 'POST' }).catch(()=>{});
          window.LR?.success(`Inserted "${tpl.name}"`);
        }
        closeTemplateMenu();
      });
    });
  }
  menu.classList.remove('hidden');
}

function closeTemplateMenu() {
  document.getElementById('templateMenu')?.classList.add('hidden');
}

async function saveAsTemplate() {
  const body = replyEditorEl.value.trim();
  if (!body) { window.LR?.error('Type a reply first'); return; }
  const name = prompt('Save this draft as a reusable template. Name?');
  if (!name) return;
  try {
    const res = await fetch('/api/templates', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name.trim(), body }),
    });
    if (!res.ok) throw new Error('Failed to save');
    templateCache = null;
    window.LR?.success(`Template "${name}" saved`);
  } catch (e) { window.LR?.error(e.message); }
}

// --- j/k navigation ---

function selectAdjacent(direction) {
  const filtered = getFilteredLeads();
  if (!filtered.length) return;
  const idx = filtered.findIndex(l => l.id === selectedLeadId);
  let next;
  if (idx < 0) next = filtered[0];
  else if (direction === 'down') next = filtered[Math.min(filtered.length - 1, idx + 1)];
  else next = filtered[Math.max(0, idx - 1)];
  if (next) selectLead(next.id);
}

document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.isContentEditable) return;
  if (e.metaKey || e.ctrlKey || e.altKey) {
    // Cmd+Enter sends draft
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      const lead = leads.find(l => l.id === selectedLeadId);
      if (lead && lead.status !== 'sent' && lead.category !== 'spam') {
        e.preventDefault();
        sendDraftReply();
      }
    }
    return;
  }
  if (e.key === 'j') { e.preventDefault(); selectAdjacent('down'); }
  else if (e.key === 'k') { e.preventDefault(); selectAdjacent('up'); }
  else if (e.key === 's' && selectedLeadId) { e.preventDefault(); toggleStar(); }
  else if (e.key === 'r') { e.preventDefault(); loadLeads(); }
  else if (e.key === 'w' && selectedLeadId) { e.preventDefault(); updatePipelineStage('won'); document.getElementById('detailPipelineStage').value = 'won'; }
  else if (e.key === 'l' && selectedLeadId) { e.preventDefault(); updatePipelineStage('lost'); document.getElementById('detailPipelineStage').value = 'lost'; }
  else if (e.key === 'e' && selectedLeadId) { e.preventDefault(); replyEditorEl?.focus(); }
});

// --- Wire new event listeners ---

document.getElementById('detailPipelineStage')?.addEventListener('change', (e) => {
  updatePipelineStage(e.target.value);
});
document.getElementById('saveDealValueBtn')?.addEventListener('click', saveDealValue);
document.getElementById('detailDealValue')?.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); saveDealValue(); }
});
document.getElementById('starToggleBtn')?.addEventListener('click', () => toggleStar());

document.getElementById('tagInput')?.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === ',') {
    e.preventDefault();
    const value = e.target.value.trim().replace(/,$/, '');
    if (value) { addTag(value); e.target.value = ''; }
  } else if (e.key === 'Backspace' && !e.target.value) {
    // Remove last tag on backspace in empty input
    const lead = leads.find(l => l.id === selectedLeadId);
    if (!lead) return;
    const tags = (lead.tags || '').split(',').map(t => t.trim()).filter(Boolean);
    if (tags.length) removeTag(tags[tags.length - 1]);
  }
});

document.getElementById('addNoteBtn')?.addEventListener('click', addNote);
document.getElementById('newNoteBody')?.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); addNote(); }
});

document.getElementById('templatePickerBtn')?.addEventListener('click', (e) => {
  e.stopPropagation();
  const menu = document.getElementById('templateMenu');
  if (menu.classList.contains('hidden')) openTemplateMenu();
  else closeTemplateMenu();
});
document.addEventListener('click', (e) => {
  if (!e.target.closest('.template-picker')) closeTemplateMenu();
});
document.getElementById('saveAsTemplateBtn')?.addEventListener('click', (e) => { e.preventDefault(); saveAsTemplate(); });

document.getElementById('cmdkBtn')?.addEventListener('click', () => window.LR?.openCmdk());

// Auto-tick SLA badges every 30s so they advance without a refresh
setInterval(() => {
  if (!leads.length) return;
  const lead = leads.find(l => l.id === selectedLeadId);
  if (lead) {
    const slaEl = document.getElementById('detailSlaTimer');
    if (slaEl && window.LR) {
      const tier = window.LR.slaTier(lead.created_at, lead.status);
      const label = tier === 'done' ? 'replied' : (window.LR.timeSince(lead.created_at) || '0m');
      slaEl.innerHTML = `<span class="sla-badge ${tier}"><span class="sla-dot"></span>${label}</span>`;
    }
  }
}, 30000);

// Allow command palette to jump to a lead on this page
window.__lrSelectLead = (id) => selectLead(id);

// If URL has ?lead=ID, select that lead after load
function applyLeadFromQuery() {
  const params = new URLSearchParams(location.search);
  const lid = params.get('lead');
  if (lid) selectedLeadId = parseInt(lid, 10);
}


// --- Init ---

applyLeadFromQuery();
checkAuth();
loadLeads().catch((error) => {
  showStatus(error.message || 'Failed to load leads', 'error');
});
loadCharts();
loadLeadMap();
startPolling();
