(function () {
  'use strict';

  const state = {
    data: null,
    activeTab: 'workspaces',
    selectedOrgId: null,
  };

  const money = new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  });
  const number = new Intl.NumberFormat();

  const els = {
    status: document.getElementById('adminStatus'),
    adminEmail: document.getElementById('adminEmail'),
    allowedAdminEmail: document.getElementById('allowedAdminEmail'),
    lastUpdated: document.getElementById('lastUpdated'),
    refresh: document.getElementById('refreshAdminBtn'),
    search: document.getElementById('adminSearch'),
    statusFilter: document.getElementById('adminStatusFilter'),
    attentionGrid: document.getElementById('attentionGrid'),
    orgRows: document.getElementById('orgRows'),
    userRows: document.getElementById('userRows'),
    leadRows: document.getElementById('leadRows'),
    callList: document.getElementById('callList'),
    smsList: document.getElementById('smsList'),
    reviewList: document.getElementById('reviewList'),
    workspaceDetail: document.getElementById('workspaceDetail'),
    kpiOrgs: document.getElementById('kpiOrgs'),
    kpiUsers: document.getElementById('kpiUsers'),
    kpiOpenLeads: document.getElementById('kpiOpenLeads'),
    kpiPipeline: document.getElementById('kpiPipeline'),
    kpiWonRevenue: document.getElementById('kpiWonRevenue'),
  };

  function escapeHtml(value) {
    if (value == null) return '';
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function titleCase(value) {
    return String(value || 'none')
      .replace(/_/g, ' ')
      .replace(/\b\w/g, (char) => char.toUpperCase());
  }

  function formatDate(value) {
    if (!value) return 'Never';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return 'Never';
    return date.toLocaleString([], {
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    });
  }

  function formatMoney(value) {
    return money.format(Number(value || 0));
  }

  function boolStatus(value) {
    return value ? '<span class="status-dot ok">Yes</span>' : '<span class="status-dot bad">No</span>';
  }

  function setStatus(message, kind = 'info') {
    if (!els.status) return;
    els.status.textContent = message;
    els.status.className = `inline-alert ${kind === 'success' ? 'success' : kind === 'error' ? 'error' : ''}`;
    els.status.classList.toggle('hidden', !message);
  }

  async function loadAdminData() {
    setStatus('Loading admin data...');
    els.refresh.disabled = true;
    try {
      const response = await fetch('/api/admin/overview?limit=150');
      if (response.status === 401) {
        window.location.href = '/login';
        return;
      }
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || 'Admin overview failed to load.');
      }
      state.data = data;
      if (!state.selectedOrgId && data.orgs.length) {
        state.selectedOrgId = data.orgs[0].id;
      }
      render();
      setStatus('');
      window.LR?.success('Admin data refreshed');
    } catch (error) {
      setStatus(error.message, 'error');
    } finally {
      els.refresh.disabled = false;
    }
  }

  function render() {
    if (!state.data) return;
    renderHeader();
    renderSummary();
    renderAttention();
    renderTables();
    renderOperations();
    renderWorkspaceDetail();
    applyFilters();
  }

  function renderHeader() {
    const admin = state.data.admin || {};
    els.adminEmail.textContent = admin.email || 'Unknown';
    els.allowedAdminEmail.textContent = (admin.allowed_emails || []).join(', ') || 'No admin email configured';
    els.lastUpdated.textContent = `Updated ${formatDate(state.data.generated_at)}`;
  }

  function renderSummary() {
    const summary = state.data.summary || {};
    els.kpiOrgs.textContent = number.format(summary.organizations || 0);
    els.kpiUsers.textContent = number.format(summary.users || 0);
    els.kpiOpenLeads.textContent = number.format(summary.open_leads || 0);
    els.kpiPipeline.textContent = formatMoney(summary.pipeline_value);
    els.kpiWonRevenue.textContent = formatMoney(summary.won_revenue);
  }

  function renderAttention() {
    const items = state.data.attention || [];
    els.attentionGrid.innerHTML = items.map((item) => {
      const severity = item.count > 0 ? 'warn' : 'ok';
      return `
        <div class="attention-card">
          <span class="status-dot ${severity}">${escapeHtml(item.label)}</span>
          <strong>${number.format(item.count || 0)}</strong>
        </div>
      `;
    }).join('');
  }

  function integrationSummary(org) {
    const it = org.integrations || {};
    const bits = [];
    if (it.gmail_connected) bits.push(`Gmail ${it.gmail_email || 'connected'}`);
    if (it.phone_numbers?.length) bits.push(`${it.phone_numbers.length} phone`);
    if (it.scheduling_enabled) bits.push('Scheduling');
    if (it.review_request_enabled) bits.push('Reviews');
    if (it.outbound_webhook_enabled) bits.push('Webhook');
    if (!bits.length) return '<span class="status-dot warn">Needs setup</span>';
    return bits.map((bit) => `<span class="pill">${escapeHtml(bit)}</span>`).join(' ');
  }

  function rowSearchText(parts) {
    return parts.filter(Boolean).join(' ').toLowerCase();
  }

  function renderTables() {
    els.orgRows.innerHTML = (state.data.orgs || []).map((org) => {
      const statusClass = org.billing_active ? 'ok' : 'bad';
      const owners = org.owner_emails?.length ? org.owner_emails.join(', ') : 'No owner';
      const search = rowSearchText([
        org.name,
        org.slug,
        owners,
        org.subscription_status,
        org.plan,
        org.integrations?.gmail_email,
      ]);
      return `
        <tr data-search="${escapeHtml(search)}" data-status="${org.billing_active ? 'active' : 'inactive'}" data-gmail="${org.integrations?.gmail_connected ? '1' : '0'}" data-open="${org.open_lead_count > 0 ? '1' : '0'}" data-selectable="true" data-org-id="${org.id}" class="${org.id === state.selectedOrgId ? 'active' : ''}">
          <td><strong>${escapeHtml(org.name)}</strong><span class="muted">${escapeHtml(org.slug)}</span></td>
          <td><span class="muted">${escapeHtml(owners)}</span></td>
          <td><span class="status-dot ${statusClass}">${escapeHtml(titleCase(org.subscription_status))}</span><br><span class="muted">${escapeHtml(org.plan)}</span></td>
          <td><strong>${number.format(org.lead_count || 0)}</strong><span class="muted">${number.format(org.open_lead_count || 0)} open, ${number.format(org.won_lead_count || 0)} won</span></td>
          <td>${integrationSummary(org)}</td>
          <td><span class="muted">${formatDate(org.latest_lead_at)}</span></td>
        </tr>
      `;
    }).join('') || '<tr><td colspan="6" class="empty-inline">No workspaces yet.</td></tr>';

    els.orgRows.querySelectorAll('[data-org-id]').forEach((row) => {
      row.addEventListener('click', () => {
        state.selectedOrgId = Number(row.dataset.orgId);
        renderTables();
        renderWorkspaceDetail();
        applyFilters();
      });
    });

    els.userRows.innerHTML = (state.data.users || []).map((user) => {
      const org = user.org || {};
      const search = rowSearchText([
        user.email,
        user.display_name,
        user.role,
        org.name,
        org.slug,
      ]);
      return `
        <tr data-search="${escapeHtml(search)}">
          <td><strong>${escapeHtml(user.display_name || user.email)}</strong><span class="muted">${escapeHtml(user.email)}</span></td>
          <td><strong>${escapeHtml(org.name || 'Missing workspace')}</strong><span class="muted">${escapeHtml(org.slug || `org ${user.org_id}`)}</span></td>
          <td><span class="pill">${escapeHtml(titleCase(user.role))}</span>${user.is_platform_admin ? ' <span class="pill">Platform admin</span>' : ''}</td>
          <td>${user.is_active ? '<span class="status-dot ok">Active</span>' : '<span class="status-dot bad">Inactive</span>'}</td>
          <td><span class="muted">${formatDate(user.created_at)}</span></td>
        </tr>
      `;
    }).join('') || '<tr><td colspan="5" class="empty-inline">No users yet.</td></tr>';

    els.leadRows.innerHTML = (state.data.recent_leads || []).map((lead) => {
      const org = lead.org || {};
      const search = rowSearchText([
        lead.sender_name,
        lead.sender_email,
        lead.phone,
        lead.subject,
        lead.body_excerpt,
        org.name,
        org.slug,
        lead.status,
        lead.category,
      ]);
      return `
        <tr data-search="${escapeHtml(search)}">
          <td><strong>${escapeHtml(lead.subject || '(no subject)')}</strong><span class="muted">${escapeHtml(lead.body_excerpt || '')}</span></td>
          <td><strong>${escapeHtml(org.name || 'Missing workspace')}</strong><span class="muted">${escapeHtml(org.slug || `org ${lead.org_id}`)}</span></td>
          <td><strong>${escapeHtml(lead.sender_name || 'Unknown')}</strong><span class="muted">${escapeHtml(lead.sender_email || '')}${lead.phone ? ` / ${escapeHtml(lead.phone)}` : ''}</span></td>
          <td><span class="pill">${escapeHtml(titleCase(lead.status))}</span><br><span class="muted">${escapeHtml(titleCase(lead.category))} / urgency ${lead.urgency_score}</span></td>
          <td><span class="muted">${lead.deal_value == null ? 'None' : formatMoney(lead.deal_value)}</span></td>
          <td><span class="muted">${formatDate(lead.created_at)}</span></td>
        </tr>
      `;
    }).join('') || '<tr><td colspan="6" class="empty-inline">No leads yet.</td></tr>';
  }

  function renderWorkspaceDetail() {
    const org = (state.data?.orgs || []).find((item) => item.id === state.selectedOrgId);
    if (!org) {
      els.workspaceDetail.innerHTML = '<p class="muted">Select a workspace to inspect its setup and live counters.</p>';
      return;
    }
    const it = org.integrations || {};
    els.workspaceDetail.innerHTML = `
      <div class="detail-row"><span>Name</span><strong>${escapeHtml(org.name)}</strong><p class="muted">${escapeHtml(org.slug)}</p></div>
      <div class="detail-row"><span>Owners</span><p>${escapeHtml((org.owner_emails || []).join(', ') || 'No owner')}</p></div>
      <div class="detail-row"><span>Billing</span><p>${org.billing_active ? '<span class="status-dot ok">Active</span>' : '<span class="status-dot bad">Inactive</span>'} ${escapeHtml(org.subscription_status)} / ${escapeHtml(org.plan)}</p></div>
      <div class="detail-row"><span>Leads</span><p>${number.format(org.open_lead_count || 0)} open of ${number.format(org.lead_count || 0)} total. ${formatMoney(org.pipeline_value)} in pipeline.</p></div>
      <div class="detail-row"><span>Gmail</span><p>${it.gmail_connected ? `<span class="status-dot ok">${escapeHtml(it.gmail_email)}</span>` : '<span class="status-dot warn">Not connected</span>'}</p></div>
      <div class="detail-row"><span>Owner alerts</span><p>${escapeHtml(it.owner_alert_email || it.sms_alert_to_number || 'Not configured')}</p></div>
      <div class="detail-row"><span>Phone</span><p>${escapeHtml((it.phone_numbers || []).join(', ') || 'No active number')}<br><span class="muted">Forwarding ${escapeHtml(titleCase(it.forwarding_status))}</span></p></div>
      <div class="integration-grid">
        <div class="integration-card"><span>Human Review</span><strong>${it.human_review ? 'On' : 'Off'}</strong></div>
        <div class="integration-card"><span>Automation</span><strong>${it.automation_paused ? 'Paused' : 'Running'}</strong></div>
        <div class="integration-card"><span>Scheduling</span><strong>${it.scheduling_enabled ? 'On' : 'Off'}</strong></div>
        <div class="integration-card"><span>Reviews</span><strong>${it.review_request_enabled ? 'On' : 'Off'}</strong></div>
      </div>
    `;
  }

  function renderMiniList(items, renderer) {
    if (!items || !items.length) return '<p class="empty-inline">No activity yet.</p>';
    return items.map(renderer).join('');
  }

  function renderOperations() {
    const ops = state.data.operations || {};
    els.callList.innerHTML = renderMiniList(ops.recent_calls, (call) => `
      <div class="mini-item" data-search="${escapeHtml(rowSearchText([call.from_number, call.to_number, call.status, call.org?.name]))}">
        <strong>${escapeHtml(call.from_number || 'Unknown caller')} to ${escapeHtml(call.to_number || 'unknown')}</strong>
        <span class="muted">${escapeHtml(call.org?.name || 'Unknown workspace')} / ${escapeHtml(titleCase(call.status))} / ${formatDate(call.started_at)}</span>
      </div>
    `);
    els.smsList.innerHTML = renderMiniList(ops.recent_sms, (sms) => `
      <div class="mini-item" data-search="${escapeHtml(rowSearchText([sms.to_number, sms.status, sms.purpose, sms.body_excerpt, sms.org?.name]))}">
        <strong>${escapeHtml(titleCase(sms.purpose))} to ${escapeHtml(sms.to_number || 'unknown')}</strong>
        <span class="muted">${escapeHtml(sms.org?.name || 'Unknown workspace')} / ${escapeHtml(titleCase(sms.status))} / ${formatDate(sms.created_at)}</span>
        ${sms.error_message ? `<span class="muted">${escapeHtml(sms.error_message)}</span>` : ''}
      </div>
    `);
    els.reviewList.innerHTML = renderMiniList(ops.review_requests, (review) => `
      <div class="mini-item" data-search="${escapeHtml(rowSearchText([review.status, review.channel, review.lead_id, review.org_id]))}">
        <strong>Lead ${escapeHtml(review.lead_id)} / ${escapeHtml(titleCase(review.status))}</strong>
        <span class="muted">${escapeHtml(titleCase(review.channel))} scheduled ${formatDate(review.scheduled_for)}</span>
        ${review.error_message ? `<span class="muted">${escapeHtml(review.error_message)}</span>` : ''}
      </div>
    `);
  }

  function setActiveTab(tab) {
    state.activeTab = tab;
    document.querySelectorAll('.admin-tab').forEach((button) => {
      button.classList.toggle('active', button.dataset.tab === tab);
    });
    document.querySelectorAll('[id^="panel-"]').forEach((panel) => {
      panel.classList.toggle('hidden', panel.id !== `panel-${tab}`);
    });
    applyFilters();
  }

  function applyFilters() {
    const q = els.search.value.trim().toLowerCase();
    const status = els.statusFilter.value;
    const activePanel = document.getElementById(`panel-${state.activeTab}`);
    if (!activePanel) return;
    activePanel.querySelectorAll('[data-search]').forEach((row) => {
      let visible = !q || row.dataset.search.includes(q);
      if (state.activeTab === 'workspaces') {
        if (status === 'active') visible = visible && row.dataset.status === 'active';
        if (status === 'inactive') visible = visible && row.dataset.status === 'inactive';
        if (status === 'gmail') visible = visible && row.dataset.gmail === '1';
        if (status === 'open') visible = visible && row.dataset.open === '1';
      }
      row.classList.toggle('hidden', !visible);
    });
  }

  document.querySelectorAll('.admin-tab').forEach((button) => {
    button.addEventListener('click', () => setActiveTab(button.dataset.tab));
  });
  els.refresh.addEventListener('click', loadAdminData);
  els.search.addEventListener('input', applyFilters);
  els.statusFilter.addEventListener('change', applyFilters);
  document.getElementById('logoutBtn')?.addEventListener('click', async () => {
    await fetch('/auth/logout', { method: 'POST' });
    window.location.href = '/login';
  });

  loadAdminData();
})();
