// Hermes Webhook Console Application Script
const API_BASE = '/api/v1';
const API_KEY_STORAGE_KEY = 'hermes_api_key';

// Application State
let state = {
    webhooks: [],
    stats: {
        total_webhooks: 0,
        pending_count: 0,
        processing_count: 0,
        completed_count: 0,
        failed_count: 0,
        success_rate: 100.0
    },
    activeFilter: 'all',
    searchQuery: '',
    selectedWebhookId: null,
    pollingInterval: null
};

let apiKey = localStorage.getItem(API_KEY_STORAGE_KEY) || '';

// DOM Cache
const dom = {
    webhooksTbody: document.getElementById('webhooks-tbody'),
    searchInput: document.getElementById('search-input'),
    refreshBtn: document.getElementById('refresh-btn'),
    filterBtns: document.querySelectorAll('.filter-btn'),
    
    // Stats Cards
    statTotal: document.getElementById('stat-total'),
    statActive: document.getElementById('stat-active'),
    statSuccess: document.getElementById('stat-success'),
    statFailed: document.getElementById('stat-failed'),
    
    // Sidebar Badges
    countAll: document.getElementById('count-all'),
    countPending: document.getElementById('count-pending'),
    countProcessing: document.getElementById('count-processing'),
    countCompleted: document.getElementById('count-completed'),
    countFailed: document.getElementById('count-failed'),
    
    // Inspector elements
    inspectorPanel: document.getElementById('inspector-panel'),
    inspectorCloseBtn: document.getElementById('inspector-close-btn'),
    inspectActions: document.getElementById('inspect-actions'),
    inspectId: document.getElementById('inspect-id'),
    inspectStatus: document.getElementById('inspect-status'),
    inspectUrl: document.getElementById('inspect-url'),
    inspectAttempts: document.getElementById('inspect-attempts'),
    inspectNextAttempt: document.getElementById('inspect-next-attempt'),
    inspectHeaders: document.getElementById('inspect-headers'),
    inspectPayload: document.getElementById('inspect-payload'),
    inspectAttemptsList: document.getElementById('inspect-attempts-list')
};

// Initialize Application
document.addEventListener('DOMContentLoaded', () => {
    setupEventListeners();
    refreshAll();
    
    // Real-time updates: Poll every 3 seconds for stats and list
    state.pollingInterval = setInterval(() => {
        refreshAll(true); // pass true to suppress full UI reloading states during polling
    }, 3000);
});

// Event Listeners
function setupEventListeners() {
    // Refresh button
    dom.refreshBtn.addEventListener('click', () => refreshAll());

    // Search bar (with debounce or keyup)
    dom.searchInput.addEventListener('input', (e) => {
        state.searchQuery = e.target.value.trim();
        fetchWebhooks();
    });

    // Sidebar filters
    dom.filterBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            dom.filterBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            state.activeFilter = btn.dataset.status;
            fetchWebhooks();
        });
    });

    // Close Inspector
    dom.inspectorCloseBtn.addEventListener('click', closeInspector);
}

// Fetch stats and lists
async function refreshAll(isPoll = false) {
    await fetchStats();
    await fetchWebhooks(isPoll);
    
    // If a webhook is currently open in the inspector, fetch its fresh details
    if (state.selectedWebhookId) {
        await fetchWebhookDetails(state.selectedWebhookId, true);
    }
}

// Fetch stats from backend API
async function fetchStats() {
    try {
        const res = await fetchApi(`${API_BASE}/stats`);
        if (!res.ok) throw new Error('Failed to fetch statistics');
        const data = await res.json();
        
        state.stats = data;
        updateStatsUI();
    } catch (err) {
        console.error('Error fetching statistics:', err);
    }
}

// Update stats in the UI
function updateStatsUI() {
    const s = state.stats;
    dom.statTotal.textContent = s.total_webhooks.toLocaleString();
    dom.statActive.textContent = (s.pending_count + s.processing_count).toLocaleString();
    dom.statSuccess.textContent = `${s.success_rate}%`;
    dom.statFailed.textContent = s.failed_count.toLocaleString();

    // Update sidebar numbers
    dom.countAll.textContent = s.total_webhooks;
    dom.countPending.textContent = s.pending_count;
    dom.countProcessing.textContent = s.processing_count;
    dom.countCompleted.textContent = s.completed_count;
    dom.countFailed.textContent = s.failed_count;
}

// Fetch webhook list from backend API
async function fetchWebhooks(isPoll = false) {
    try {
        let url = `${API_BASE}/webhooks?limit=100`;
        if (state.activeFilter !== 'all') {
            url += `&status=${state.activeFilter}`;
        }
        
        const res = await fetchApi(url);
        if (!res.ok) throw new Error('Failed to fetch webhooks list');
        const data = await res.json();
        
        let filteredWebhooks = data.webhooks;
        
        // Filter locally by Search Query if set
        if (state.searchQuery) {
            const query = state.searchQuery.toLowerCase();
            filteredWebhooks = filteredWebhooks.filter(w => 
                w.destination_url.toLowerCase().includes(query) ||
                w.id.toLowerCase().includes(query)
            );
        }
        
        state.webhooks = filteredWebhooks;
        renderWebhooksTable(isPoll);
    } catch (err) {
        console.error('Error fetching webhooks:', err);
    }
}

// Render the list of webhooks in the main table
function renderWebhooksTable(isPoll = false) {
    if (state.webhooks.length === 0) {
        dom.webhooksTbody.innerHTML = `
            <tr>
                <td colspan="5" style="text-align: center; color: var(--text-muted); padding: 40px 0;">
                    No webhooks match the current filters.
                </td>
            </tr>
        `;
        return;
    }

    // Keep track of the current selected row index so it doesn't lose highlight on refresh
    const rowsHtml = state.webhooks.map(w => {
        const isSelected = w.id === state.selectedWebhookId ? 'selected' : '';
        return `
            <tr class="table-row ${isSelected}" data-id="${w.id}" onclick="handleRowClick('${w.id}')">
                <td>${getStatusBadge(w.status)}</td>
                <td><span class="url-text mono" title="${w.destination_url}">${w.destination_url}</span></td>
                <td style="text-align: right;" class="mono">${w.retry_count}/${w.max_retries}</td>
                <td class="mono">${formatDateTime(w.last_attempt_at) || '<span style="color: var(--text-muted);">Never attempted</span>'}</td>
                <td class="mono">${formatDateTime(w.created_at)}</td>
            </tr>
        `;
    }).join('');

    dom.webhooksTbody.innerHTML = rowsHtml;
}

// Handle clicking on a row to inspect a webhook
async function handleRowClick(id) {
    // Toggle highlight
    document.querySelectorAll('.table-row').forEach(row => {
        row.classList.remove('selected');
        if (row.dataset.id === id) {
            row.classList.add('selected');
        }
    });

    state.selectedWebhookId = id;
    await fetchWebhookDetails(id);
}

// Fetch full detail of a webhook (payload, headers, attempts)
async function fetchWebhookDetails(id, isPoll = false) {
    try {
        const res = await fetchApi(`${API_BASE}/webhooks/${id}`);
        if (!res.ok) throw new Error('Failed to fetch details');
        const webhook = await res.json();
        
        renderInspector(webhook);
        
        if (!isPoll) {
            dom.inspectorPanel.style.display = 'flex';
        }
    } catch (err) {
        console.error('Error fetching webhook details:', err);
    }
}

// Render the details in the split inspector panel
function renderInspector(w) {
    dom.inspectId.textContent = w.id;
    dom.inspectStatus.innerHTML = getStatusBadge(w.status);
    dom.inspectUrl.textContent = w.destination_url;
    dom.inspectAttempts.textContent = `${w.retry_count} / ${w.max_retries}`;
    dom.inspectNextAttempt.textContent = w.status === 'pending' ? formatDateTime(w.next_attempt_at) : 'N/A';
    
    // Format JSON blocks
    dom.inspectHeaders.textContent = JSON.stringify(w.headers, null, 2);
    dom.inspectPayload.textContent = JSON.stringify(w.payload, null, 2);
    
    // Add actions (e.g. Replay button for failed webhooks)
    dom.inspectActions.innerHTML = '';
    if (w.status === 'failed' || w.status === 'completed') {
        const replayBtn = document.createElement('button');
        replayBtn.className = 'btn btn-secondary';
        replayBtn.style.flex = '1';
        replayBtn.innerHTML = `
            <svg style="width:14px;height:14px;fill:none;stroke:currentColor;stroke-width:2" viewBox="0 0 24 24">
                <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l.73-.73" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            Force Replay
        `;
        replayBtn.addEventListener('click', () => handleReplay(w.id));
        dom.inspectActions.appendChild(replayBtn);
    }

    // Render historical attempts list
    if (!w.attempts || w.attempts.length === 0) {
        dom.inspectAttemptsList.innerHTML = `<span style="color: var(--text-muted); font-size:12px;">No delivery attempts logged yet.</span>`;
    } else {
        dom.inspectAttemptsList.innerHTML = w.attempts.map((att, idx) => {
            const isSuccess = att.status_code && att.status_code >= 200 && att.status_code < 300;
            const statusClass = isSuccess ? 'attempt-success' : 'attempt-failed';
            const statusText = isSuccess ? `Success (${att.status_code})` : (att.status_code ? `Error (${att.status_code})` : 'Failed');
            
            return `
                <div class="attempt-card">
                    <div class="attempt-header">
                        <span class="font-weight-bold">Attempt #${att.attempt_number}</span>
                        <span class="${statusClass} font-weight-bold">${statusText}</span>
                    </div>
                    <div class="metadata-grid" style="grid-template-columns: 80px 1fr; margin-top: 4px;">
                        <span class="metadata-label">Time</span>
                        <span class="metadata-value mono">${formatDateTime(att.attempted_at)}</span>
                        
                        <span class="metadata-label">Duration</span>
                        <span class="metadata-value mono">${att.duration_ms ? `${att.duration_ms}ms` : 'N/A'}</span>
                        
                        ${att.error_message ? `
                            <span class="metadata-label">Error</span>
                            <span class="metadata-value" style="color: var(--color-rose);">${att.error_message}</span>
                        ` : ''}
                    </div>
                    ${att.response_body ? `
                        <div style="margin-top: 8px;">
                            <span class="metadata-label" style="display:block; margin-bottom: 2px;">Response Body Snippet:</span>
                            <pre style="max-height: 80px; padding: 6px; font-size: 11px;"><code>${escapeHtml(att.response_body)}</code></pre>
                        </div>
                    ` : ''}
                </div>
            `;
        }).join('');
    }
}

// Handle trigger manual replay
async function handleReplay(id) {
    try {
        const res = await fetchApi(`${API_BASE}/webhooks/${id}/replay`, { method: 'POST' });
        if (!res.ok) throw new Error('Replay trigger failed');
        
        // Show immediate loading/pending state
        refreshAll();
    } catch (err) {
        alert(`Failed to trigger replay: ${err.message}`);
    }
}

async function fetchApi(url, options = {}) {
    const headers = new Headers(options.headers || {});
    if (apiKey) {
        headers.set('X-Hermes-API-Key', apiKey);
    }

    let response = await fetch(url, { ...options, headers });
    if (response.status !== 401) {
        return response;
    }

    const nextKey = prompt('Enter Hermes API key');
    if (!nextKey) {
        return response;
    }

    apiKey = nextKey.trim();
    localStorage.setItem(API_KEY_STORAGE_KEY, apiKey);
    headers.set('X-Hermes-API-Key', apiKey);
    response = await fetch(url, { ...options, headers });

    if (response.status === 401) {
        localStorage.removeItem(API_KEY_STORAGE_KEY);
        apiKey = '';
    }

    return response;
}

// Close Details Inspector panel
function closeInspector() {
    dom.inspectorPanel.style.display = 'none';
    state.selectedWebhookId = null;
    document.querySelectorAll('.table-row').forEach(row => row.classList.remove('selected'));
}

// Helpers
function getStatusBadge(status) {
    const s = status.toLowerCase();
    return `<span class="badge badge-${s}">${s}</span>`;
}

function formatDateTime(isoString) {
    if (!isoString) return null;
    const date = new Date(isoString);
    return date.toLocaleString();
}

function escapeHtml(text) {
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };
    return text.replace(/[&<>"']/g, function(m) { return map[m]; });
}
