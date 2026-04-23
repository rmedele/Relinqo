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

let leads = [];
let threads = {};
let selectedLeadId = null;
let pollInterval = null;
let undoTimer = null;
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
  sendStatusEl.textContent = message;
  sendStatusEl.className = `send-status ${kind}`;
  sendStatusEl.classList.remove('hidden');
}

function clearStatus() {
  sendStatusEl.className = 'send-status hidden';
  sendStatusEl.textContent = '';
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
    button.innerHTML = `
      <div class="lead-item-top">
        <div>
          <strong>${pendingIcon}${escapeHtml(lead.sender_name) || 'Unknown sender'}</strong>
          <p class="muted">${escapeHtml(lead.sender_email)}</p>
        </div>
        <span class="badge ${statusClass(lead.status)}">${titleCase(lead.status)}</span>
      </div>
      <p class="lead-subject">${escapeHtml(lead.subject) || '(no subject)'}</p>
      <div class="lead-item-bottom">
        <span class="muted">${titleCase(lead.category)} \u2022 ${urgencyLabel(lead.urgency_score)} priority</span>
        <span class="muted">${titleCase(lead.source)}</span>
      </div>
    `;
    button.addEventListener('click', () => selectLead(lead.id));
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

function selectLead(id) {
  selectedLeadId = id;
  renderLeadList();
  renderLeadDetail();
  // On mobile (stacked layout), scroll the detail panel into view so the user
  // doesn't have to scroll past the entire lead list after tapping.
  if (window.matchMedia('(max-width: 1180px)').matches) {
    requestAnimationFrame(() => {
      leadDetailEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  }
}

// --- Data Loading ---

async function loadLeads() {
  if (!leads.length) {
    loadingStateEl.classList.remove('hidden');
    emptyStateEl.classList.add('hidden');
    leadDetailEl.classList.add('hidden');
  }

  const response = await fetch(`/leads?page=${currentPage}&page_size=${pageSize}`);
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
filterCategory.addEventListener('change', renderLeadList);
filterStatus.addEventListener('change', renderLeadList);
filterUrgency.addEventListener('change', renderLeadList);

// --- Auth ---

async function checkAuth() {
  try {
    const res = await fetch('/auth/me');
    if (!res.ok) { window.location.href = '/login'; return; }
    const data = await res.json();
    const orgNameEl = document.getElementById('orgName');
    if (orgNameEl) orgNameEl.textContent = data.org.name;
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


// --- Init ---

checkAuth();
loadLeads().catch((error) => {
  showStatus(error.message || 'Failed to load leads', 'error');
});
loadCharts();
startPolling();
