/* Reusable UI components */

function toast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    el.textContent = message;
    container.appendChild(el);
    setTimeout(() => el.remove(), 4000);
}

function openModal(title, bodyHtml) {
    document.getElementById('modal-title').textContent = title;
    document.getElementById('modal-body').innerHTML = bodyHtml;
    document.getElementById('modal-overlay').classList.remove('hidden');
}

function closeModal(event) {
    if (event && event.target !== document.getElementById('modal-overlay')) return;
    document.getElementById('modal-overlay').classList.add('hidden');
}

function statusBadge(status) {
    const map = {
        completed: 'success', success: 'success', active: 'success', running: 'info',
        pending: 'neutral', waiting: 'neutral', paused: 'warning',
        interrupted: 'warning', skipped: 'warning', zero_output: 'warning',
        failed: 'danger', failed_terminal: 'danger', failed_retryable: 'warning',
        cancelled: 'neutral', timeout: 'danger', critical: 'danger',
        warning: 'warning', info: 'info',
    };
    const cls = map[status] || 'neutral';
    return `<span class="badge badge-${cls}">${status}</span>`;
}

function formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

function formatDuration(seconds) {
    if (seconds < 60) return `${seconds.toFixed(1)}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
    return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

function formatDate(iso) {
    if (!iso) return '-';
    const d = new Date(iso);
    return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function progressBar(value, max, cls = 'success') {
    const pct = max > 0 ? (value / max * 100) : 0;
    return `<div class="progress-bar"><div class="progress-fill ${cls}" style="width:${pct}%"></div></div>`;
}

function tagsHtml(tags) {
    if (!tags || tags.length === 0) return '<span class="text-muted text-sm">none</span>';
    return tags.map(t => `<span class="tag">${t}</span>`).join('');
}

function loading() {
    return '<div class="loading-state"><div class="spinner"></div><p class="mt-8">Loading...</p></div>';
}

function emptyState(msg, sub = '') {
    return `<div class="empty-state"><h3>${msg}</h3>${sub ? `<p>${sub}</p>` : ''}</div>`;
}

function renderTable(headers, rows) {
    if (rows.length === 0) return emptyState('No data', 'Nothing to display yet.');
    let html = '<div class="table-container"><table><thead><tr>';
    headers.forEach(h => html += `<th>${h}</th>`);
    html += '</tr></thead><tbody>';
    rows.forEach(cells => {
        html += '<tr>';
        cells.forEach(c => html += `<td>${c}</td>`);
        html += '</tr>';
    });
    html += '</tbody></table></div>';
    return html;
}

function renderTabs(tabs, activeTab, onClickFn) {
    let html = '<div class="tabs">';
    tabs.forEach(t => {
        const cls = t.id === activeTab ? 'tab active' : 'tab';
        html += `<div class="${cls}" onclick="${onClickFn}('${t.id}')">${t.label}</div>`;
    });
    html += '</div>';
    return html;
}
