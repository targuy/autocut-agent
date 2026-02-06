/* Page renderers for each section of the GUI */

// =========================================================================
// DASHBOARD
// =========================================================================
async function renderDashboard() {
    const content = document.getElementById('content');
    content.innerHTML = loading();
    try {
        let health = {}, pipelines = [], advisories = {};
        try { health = await api.system.health(); } catch(e) { health = { status: 'unknown' }; }
        try { pipelines = await api.pipelines.list(); } catch(e) {}
        try { advisories = await api.system.advisories(); } catch(e) {}

        const running = pipelines.filter(p => p.status === 'running').length;
        const completed = pipelines.filter(p => p.status === 'completed').length;
        const failed = pipelines.filter(p => p.status === 'failed').length;
        const totalAdvisories = Object.values(advisories).flat().length;

        content.innerHTML = `
            <div class="page-header"><h1>Dashboard</h1><span class="text-sm text-muted">${new Date().toLocaleString()}</span></div>
            <div class="stats-grid">
                <div class="stat-card accent"><div class="stat-value">${pipelines.length}</div><div class="stat-label">Total Pipelines</div></div>
                <div class="stat-card success"><div class="stat-value">${running}</div><div class="stat-label">Running</div></div>
                <div class="stat-card success"><div class="stat-value">${completed}</div><div class="stat-label">Completed</div></div>
                <div class="stat-card ${failed > 0 ? 'danger' : ''}"><div class="stat-value">${failed}</div><div class="stat-label">Failed</div></div>
                <div class="stat-card ${totalAdvisories > 0 ? 'warning' : ''}"><div class="stat-value">${totalAdvisories}</div><div class="stat-label">Advisories</div></div>
                <div class="stat-card"><div class="stat-value">${statusBadge(health.status || 'unknown')}</div><div class="stat-label">System Health</div></div>
            </div>
            <div class="card">
                <div class="card-header"><h3>Recent Pipelines</h3></div>
                ${renderTable(['Name', 'Status', 'Created'],
                    pipelines.slice(0, 10).map(p => [
                        `<a href="#" onclick="viewPipeline('${p.id}')">${p.name}</a>`,
                        statusBadge(p.status),
                        formatDate(p.created_at)
                    ])
                )}
            </div>
            ${totalAdvisories > 0 ? `
            <div class="card">
                <div class="card-header"><h3>Active Advisories</h3></div>
                ${Object.entries(advisories).map(([name, list]) =>
                    list.map(a => `
                        <div class="step-item ${a.severity === 'critical' ? 'failed' : 'pending'}">
                            <span class="step-order">${statusBadge(a.severity)}</span>
                            <span class="step-name"><strong>${name}</strong>: ${a.title}</span>
                            <span class="step-type">${a.based_on_runs} runs</span>
                        </div>
                    `).join('')
                ).join('')}
            </div>` : ''}
        `;
    } catch (e) {
        content.innerHTML = `<div class="page-header"><h1>Dashboard</h1></div><div class="card"><p>Backend not available. Start the agent server to see live data.</p><p class="text-sm text-muted mt-8">${e.message}</p></div>`;
    }
}

// =========================================================================
// PIPELINES
// =========================================================================
async function renderPipelines() {
    const content = document.getElementById('content');
    content.innerHTML = loading();
    try {
        const pipelines = await api.pipelines.list();
        content.innerHTML = `
            <div class="page-header">
                <h1>Pipelines</h1>
                <button class="btn btn-primary" onclick="navigateTo('compile')">+ Compile New</button>
            </div>
            ${renderTable(['Name', 'Status', 'Created', 'Actions'],
                pipelines.map(p => [
                    `<a href="#" onclick="viewPipeline('${p.id}')">${p.name}</a>`,
                    statusBadge(p.status),
                    formatDate(p.created_at),
                    `<div class="btn-group">
                        <button class="btn btn-sm btn-secondary" onclick="viewPipeline('${p.id}')">View</button>
                        ${p.status === 'interrupted' ? `<button class="btn btn-sm btn-primary" onclick="resumePipeline('${p.id}')">Resume</button>` : ''}
                        ${p.status === 'running' ? `<button class="btn btn-sm btn-danger" onclick="cancelPipeline('${p.id}')">Cancel</button>` : ''}
                    </div>`
                ])
            )}
        `;
    } catch (e) {
        content.innerHTML = `<div class="page-header"><h1>Pipelines</h1></div>${emptyState('Could not load pipelines', e.message)}`;
    }
}

async function viewPipeline(id) {
    const content = document.getElementById('content');
    content.innerHTML = loading();
    try {
        const p = await api.pipelines.get(id);
        content.innerHTML = `
            <div class="page-header">
                <h1>${p.name}</h1>
                <div class="btn-group">
                    ${p.status === 'interrupted' ? `<button class="btn btn-primary" onclick="resumePipeline('${id}')">Resume</button>` : ''}
                    ${p.status === 'running' ? `<button class="btn btn-danger" onclick="cancelPipeline('${id}')">Cancel</button>` : ''}
                    <button class="btn btn-secondary" onclick="navigateTo('pipelines')">Back</button>
                </div>
            </div>
            <div class="stats-grid">
                <div class="stat-card"><div class="stat-value">${statusBadge(p.status)}</div><div class="stat-label">Status</div></div>
                <div class="stat-card"><div class="stat-value">${p.steps ? p.steps.length : 0}</div><div class="stat-label">Steps</div></div>
                <div class="stat-card"><div class="stat-value">${formatDate(p.created_at)}</div><div class="stat-label">Created</div></div>
            </div>
            <div class="card">
                <div class="card-header"><h3>Steps</h3></div>
                <div class="step-list">
                    ${(p.steps || []).map(s => `
                        <div class="step-item ${s.status}">
                            <span class="step-order">#${s.order + 1}</span>
                            <span class="step-name">${s.name}</span>
                            <span class="step-type">${s.command_type}</span>
                            ${statusBadge(s.status)}
                            ${['failed_terminal', 'interrupted', 'failed_retryable'].includes(s.status) ?
                                `<button class="btn btn-sm btn-secondary" onclick="skipStep('${id}','${s.id}')">Skip</button>` : ''}
                        </div>
                    `).join('')}
                </div>
            </div>
            ${p.inputs && Object.keys(p.inputs).length > 0 ? `
            <div class="card">
                <div class="card-header"><h3>Inputs</h3></div>
                <pre class="mono" style="padding:8px;background:var(--bg-input);border-radius:6px">${JSON.stringify(p.inputs, null, 2)}</pre>
            </div>` : ''}
        `;
    } catch (e) {
        content.innerHTML = emptyState('Pipeline not found', e.message);
    }
}

async function resumePipeline(id) {
    try { await api.pipelines.resume(id); toast('Pipeline resumed', 'success'); viewPipeline(id); } catch(e) { toast(e.message, 'error'); }
}
async function cancelPipeline(id) {
    try { await api.pipelines.cancel(id); toast('Pipeline cancelled', 'success'); viewPipeline(id); } catch(e) { toast(e.message, 'error'); }
}
async function skipStep(pid, sid) {
    try { await api.pipelines.skipStep(pid, sid); toast('Step skipped', 'success'); viewPipeline(pid); } catch(e) { toast(e.message, 'error'); }
}

// =========================================================================
// TEMPLATES
// =========================================================================
async function renderTemplates() {
    const content = document.getElementById('content');
    content.innerHTML = loading();
    try {
        const templates = await api.templates.list();
        content.innerHTML = `
            <div class="page-header"><h1>Pipeline Templates</h1></div>
            ${renderTable(['Name', 'Description', 'Steps', 'Version', 'Created By', 'Actions'],
                templates.map(t => [
                    t.name,
                    `<span class="text-sm">${(t.description || '').substring(0, 60)}</span>`,
                    t.steps ? t.steps.length : '-',
                    `v${t.version}`,
                    `<span class="badge badge-accent">${t.created_by}</span>`,
                    `<div class="btn-group">
                        <button class="btn btn-sm btn-primary" onclick="cloneTemplate('${t.id}')">Clone</button>
                        <button class="btn btn-sm btn-danger" onclick="deleteTemplate('${t.id}')">Delete</button>
                    </div>`
                ])
            )}
        `;
    } catch (e) {
        content.innerHTML = `<div class="page-header"><h1>Templates</h1></div>${emptyState('Could not load templates', e.message)}`;
    }
}

async function cloneTemplate(id) {
    openModal('Clone Template', `
        <div class="form-group"><label>Inputs (JSON)</label><textarea class="form-control" id="clone-inputs" placeholder='{"video_path": "/path/to/video.mp4"}'>{}</textarea></div>
        <button class="btn btn-primary" onclick="doCloneTemplate('${id}')">Clone & Run</button>
    `);
}
async function doCloneTemplate(id) {
    try {
        const inputs = JSON.parse(document.getElementById('clone-inputs').value);
        await api.templates.clone(id, inputs);
        closeModal(); toast('Template cloned and pipeline started', 'success'); renderPipelines();
    } catch(e) { toast(e.message, 'error'); }
}
async function deleteTemplate(id) {
    if (!confirm('Delete this template?')) return;
    try { await api.templates.del(id); toast('Template deleted', 'success'); renderTemplates(); } catch(e) { toast(e.message, 'error'); }
}

// =========================================================================
// PROGRAMS (Registry)
// =========================================================================
let programsTab = 'list';

async function renderPrograms() {
    const content = document.getElementById('content');
    content.innerHTML = loading();

    const tabs = [
        { id: 'list', label: 'Programs' },
        { id: 'register', label: '+ Register' },
        { id: 'advisories', label: 'Advisories' },
    ];

    let body = '';
    try {
        if (programsTab === 'list') {
            const programs = await api.programs.list(false);
            body = renderTable(['Name', 'Type', 'Tags', 'Version', 'Active', 'Actions'],
                programs.map(p => [
                    `<a href="#" onclick="viewProgram('${p.name}')">${p.name}</a>`,
                    `<span class="badge badge-info">${p.command_type}</span>`,
                    tagsHtml(p.tags),
                    p.version,
                    p.active ? statusBadge('active') : statusBadge('cancelled'),
                    `<div class="btn-group">
                        <button class="btn btn-sm btn-secondary" onclick="viewProgram('${p.name}')">Edit</button>
                        <button class="btn btn-sm btn-secondary" onclick="viewProgramStats('${p.name}')">Stats</button>
                    </div>`
                ])
            );
        } else if (programsTab === 'register') {
            body = renderRegisterForm();
        } else if (programsTab === 'advisories') {
            const advs = await api.system.advisories();
            const allAdvs = Object.entries(advs).flatMap(([name, list]) => list.map(a => ({...a, program_name: name})));
            body = renderTable(['Program', 'Severity', 'Title', 'Message', 'Runs'],
                allAdvs.map(a => [
                    a.program_name,
                    statusBadge(a.severity),
                    a.title,
                    `<span class="text-sm">${a.message.substring(0, 100)}...</span>`,
                    a.based_on_runs,
                ])
            );
        }
    } catch(e) {
        body = emptyState('Could not load programs', e.message);
    }

    content.innerHTML = `
        <div class="page-header"><h1>Program Registry</h1></div>
        ${renderTabs(tabs, programsTab, 'switchProgramsTab')}
        ${body}
    `;
}

function switchProgramsTab(tab) { programsTab = tab; renderPrograms(); }

function renderRegisterForm() {
    return `
    <div class="card">
        <h3 style="margin-bottom:16px">Register New Program</h3>
        <div class="form-row">
            <div class="form-group"><label>Name</label><input class="form-control" id="reg-name" placeholder="facedetection"></div>
            <div class="form-group"><label>Command Type</label>
                <select class="form-control" id="reg-cmdtype"><option>python</option><option>ffmpeg</option><option>shell</option></select>
            </div>
        </div>
        <div class="form-group"><label>Description</label><input class="form-control" id="reg-desc" placeholder="What does this program do?"></div>
        <div class="form-group"><label>Purpose</label><input class="form-control" id="reg-purpose" placeholder="When/why to use it"></div>
        <div class="form-group"><label>Command Template</label><input class="form-control" id="reg-cmd" placeholder="python facedetect.py --input {input_path} --threshold {threshold}"></div>
        <div class="form-row">
            <div class="form-group"><label>Required Inputs (comma-sep)</label><input class="form-control" id="reg-inputs" placeholder="clip_path, timecodes"></div>
            <div class="form-group"><label>Expected Outputs (comma-sep)</label><input class="form-control" id="reg-outputs" placeholder="filtered_clips, face_count"></div>
        </div>
        <div class="form-group"><label>Tags (comma-sep)</label><input class="form-control" id="reg-tags" placeholder="filter, video, gpu"></div>
        <div class="form-group">
            <label>Parameters (one per line: name:type:default:description)</label>
            <textarea class="form-control" id="reg-params" rows="4" placeholder="threshold:float:0.5:Detection confidence\nmax_faces:int:10:Maximum faces to detect"></textarea>
        </div>
        <button class="btn btn-primary" onclick="doRegisterProgram()">Register Program</button>
    </div>`;
}

async function doRegisterProgram() {
    const parseList = v => v ? v.split(',').map(s => s.trim()).filter(Boolean) : [];
    const parseParams = text => {
        if (!text.trim()) return [];
        return text.trim().split('\n').filter(Boolean).map(line => {
            const [name, type, def, desc] = line.split(':', 4);
            const p = { name: name.trim(), type: (type || 'string').trim(), description: (desc || '').trim() };
            if (def && def.trim()) {
                if (p.type === 'int') p.default = parseInt(def.trim());
                else if (p.type === 'float') p.default = parseFloat(def.trim());
                else if (p.type === 'bool') p.default = def.trim().toLowerCase() === 'true';
                else p.default = def.trim();
            }
            return p;
        });
    };
    try {
        await api.programs.register({
            name: document.getElementById('reg-name').value,
            command_type: document.getElementById('reg-cmdtype').value,
            description: document.getElementById('reg-desc').value,
            purpose: document.getElementById('reg-purpose').value,
            command_template: document.getElementById('reg-cmd').value,
            required_inputs: parseList(document.getElementById('reg-inputs').value),
            expected_outputs: parseList(document.getElementById('reg-outputs').value),
            tags: parseList(document.getElementById('reg-tags').value),
            parameters: parseParams(document.getElementById('reg-params').value),
        });
        toast('Program registered', 'success');
        programsTab = 'list'; renderPrograms();
    } catch(e) { toast(e.message, 'error'); }
}

async function viewProgram(name) {
    const content = document.getElementById('content');
    content.innerHTML = loading();
    try {
        const prog = await api.programs.get(name);
        let statsHtml = '';
        try {
            const stats = await api.programs.stats(name);
            statsHtml = `
            <div class="stats-grid">
                <div class="stat-card"><div class="stat-value">${stats.total_runs}</div><div class="stat-label">Total Runs</div></div>
                <div class="stat-card success"><div class="stat-value">${(stats.success_rate * 100).toFixed(0)}%</div><div class="stat-label">Success Rate</div></div>
                <div class="stat-card ${stats.zero_output_rate > 0.5 ? 'warning' : ''}"><div class="stat-value">${(stats.zero_output_rate * 100).toFixed(0)}%</div><div class="stat-label">Zero Output</div></div>
                <div class="stat-card"><div class="stat-value">${formatDuration(stats.avg_duration_seconds)}</div><div class="stat-label">Avg Duration</div></div>
            </div>`;
        } catch(e) {}

        content.innerHTML = `
            <div class="page-header">
                <h1>${prog.name}</h1>
                <div class="btn-group">
                    <button class="btn btn-secondary" onclick="renderPrograms()">Back</button>
                    <button class="btn btn-danger" onclick="deactivateProgram('${prog.name}')">Deactivate</button>
                </div>
            </div>
            <div class="card">
                <p><strong>Description:</strong> ${prog.description || '-'}</p>
                <p class="mt-8"><strong>Purpose:</strong> ${prog.purpose || '-'}</p>
                <p class="mt-8"><strong>Command:</strong> <code class="mono">${prog.command_template}</code></p>
                <p class="mt-8"><strong>Type:</strong> ${statusBadge(prog.command_type)} <strong>Tags:</strong> ${tagsHtml(prog.tags)}</p>
            </div>
            ${statsHtml}
            <div class="card">
                <div class="card-header">
                    <h3>Parameters</h3>
                    <button class="btn btn-sm btn-primary" onclick="showAddParamForm('${prog.name}')">+ Add</button>
                </div>
                ${prog.parameters.length > 0 ? renderTable(
                    ['Name', 'Type', 'Default', 'Current', 'Description', 'Actions'],
                    prog.parameters.map(p => [
                        p.name,
                        `<span class="badge badge-info">${p.type}</span>`,
                        p.default !== null ? p.default : '-',
                        p.current_value !== null ? `<strong>${p.current_value}</strong>` : '-',
                        p.description || '-',
                        `<button class="btn btn-sm btn-secondary" onclick="editParam('${prog.name}','${p.name}','${p.current_value || p.default || ''}')">Edit</button>`
                    ])
                ) : emptyState('No parameters', 'Add parameters to configure this program.')}
            </div>
            <div class="card">
                <div class="card-header">
                    <h3>Inject Scores (Training)</h3>
                </div>
                <p class="text-sm text-muted mb-16">Manually inject execution scores to accelerate the learning process.</p>
                <div class="form-row">
                    <div class="form-group"><label>Outcome</label>
                        <select class="form-control" id="score-outcome">
                            <option value="success">Success</option>
                            <option value="failure">Failure</option>
                            <option value="zero_output">Zero Output</option>
                            <option value="timeout">Timeout</option>
                        </select>
                    </div>
                    <div class="form-group"><label>Duration (s)</label><input class="form-control" id="score-duration" type="number" value="10"></div>
                </div>
                <div class="form-group"><label>Parameters Used (JSON)</label><input class="form-control" id="score-params" value='${JSON.stringify(Object.fromEntries(prog.parameters.map(p => [p.name, p.current_value || p.default])))}' /></div>
                <div class="form-group"><label>Count (inject multiple)</label><input class="form-control" id="score-count" type="number" value="1" min="1" max="100"></div>
                <button class="btn btn-primary" onclick="injectScores('${prog.name}')">Inject Scores</button>
                <button class="btn btn-danger" onclick="clearScores('${prog.name}')">Clear All Scores</button>
            </div>
        `;
    } catch(e) {
        content.innerHTML = emptyState('Program not found', e.message);
    }
}

async function editParam(progName, paramName, currentVal) {
    openModal(`Edit ${paramName}`, `
        <div class="form-group"><label>New Value</label><input class="form-control" id="param-val" value="${currentVal}"></div>
        <button class="btn btn-primary" onclick="doEditParam('${progName}','${paramName}')">Save</button>
    `);
}
async function doEditParam(progName, paramName) {
    try {
        let val = document.getElementById('param-val').value;
        await api.programs.updateParams(progName, { [paramName]: val });
        closeModal(); toast('Parameter updated', 'success'); viewProgram(progName);
    } catch(e) { toast(e.message, 'error'); }
}

function showAddParamForm(progName) {
    openModal('Add Parameter', `
        <div class="form-row">
            <div class="form-group"><label>Name</label><input class="form-control" id="ap-name"></div>
            <div class="form-group"><label>Type</label>
                <select class="form-control" id="ap-type"><option>string</option><option>int</option><option>float</option><option>bool</option><option>enum</option><option>path</option></select>
            </div>
        </div>
        <div class="form-group"><label>Description</label><input class="form-control" id="ap-desc"></div>
        <div class="form-group"><label>Default Value</label><input class="form-control" id="ap-default"></div>
        <button class="btn btn-primary" onclick="doAddParam('${progName}')">Add</button>
    `);
}
async function doAddParam(progName) {
    try {
        await api.programs.addParam(progName, {
            name: document.getElementById('ap-name').value,
            type: document.getElementById('ap-type').value,
            description: document.getElementById('ap-desc').value,
            default: document.getElementById('ap-default').value || null,
        });
        closeModal(); toast('Parameter added', 'success'); viewProgram(progName);
    } catch(e) { toast(e.message, 'error'); }
}

async function injectScores(progName) {
    try {
        const count = parseInt(document.getElementById('score-count').value) || 1;
        const score = {
            outcome: document.getElementById('score-outcome').value,
            parameters_used: JSON.parse(document.getElementById('score-params').value || '{}'),
            duration_seconds: parseFloat(document.getElementById('score-duration').value) || 0,
        };
        if (count === 1) {
            await api.programs.injectScore(progName, score);
        } else {
            const scores = Array(count).fill(score);
            await api.programs.injectBatch(progName, scores);
        }
        toast(`${count} score(s) injected`, 'success');
        viewProgram(progName);
    } catch(e) { toast(e.message, 'error'); }
}

async function clearScores(progName) {
    if (!confirm(`Clear ALL scores for ${progName}?`)) return;
    try { await api.programs.clearScores(progName); toast('Scores cleared', 'success'); viewProgram(progName); } catch(e) { toast(e.message, 'error'); }
}

async function viewProgramStats(name) {
    try {
        const [stats, paramSets, advs] = await Promise.all([
            api.programs.stats(name),
            api.programs.paramSets(name),
            api.programs.advisories(name),
        ]);
        let html = `<div class="stats-grid">
            <div class="stat-card"><div class="stat-value">${stats.total_runs}</div><div class="stat-label">Total Runs</div></div>
            <div class="stat-card success"><div class="stat-value">${(stats.success_rate*100).toFixed(0)}%</div><div class="stat-label">Success</div></div>
            <div class="stat-card warning"><div class="stat-value">${(stats.zero_output_rate*100).toFixed(0)}%</div><div class="stat-label">Zero Output</div></div>
            <div class="stat-card danger"><div class="stat-value">${(stats.failure_rate*100).toFixed(0)}%</div><div class="stat-label">Failure</div></div>
        </div>`;
        if (paramSets.length > 0) {
            html += '<h4 style="margin:16px 0 8px">Per-Parameter-Set Stats</h4>';
            html += renderTable(['Parameters', 'Runs', 'Success Rate', 'Zero Output', 'Avg Duration'],
                paramSets.map(s => [
                    `<code class="mono text-sm">${JSON.stringify(s.parameters)}</code>`,
                    s.total_runs,
                    `${(s.success_rate*100).toFixed(0)}%`,
                    s.zero_outputs,
                    formatDuration(s.avg_duration_seconds),
                ])
            );
        }
        if (advs.length > 0) {
            html += '<h4 style="margin:16px 0 8px">Advisories</h4>';
            advs.forEach(a => {
                html += `<div class="step-item ${a.severity === 'critical' ? 'failed' : 'pending'}">
                    ${statusBadge(a.severity)} <strong>${a.title}</strong> — ${a.message}
                </div>`;
            });
        }
        openModal(`Stats: ${name}`, html);
    } catch(e) { toast(e.message, 'error'); }
}

async function deactivateProgram(name) {
    if (!confirm(`Deactivate ${name}?`)) return;
    try { await api.programs.deactivate(name); toast('Program deactivated', 'success'); renderPrograms(); } catch(e) { toast(e.message, 'error'); }
}

// =========================================================================
// STORAGE
// =========================================================================
async function renderStorage() {
    const content = document.getElementById('content');
    content.innerHTML = loading();
    try {
        let files = [], stats = {};
        try { files = await api.storage.list(); } catch(e) {}
        try { stats = await api.storage.stats(); } catch(e) {}

        content.innerHTML = `
            <div class="page-header">
                <h1>Media Storage</h1>
                <button class="btn btn-primary" onclick="showIngestForm()">+ Ingest File</button>
            </div>
            <div class="stats-grid">
                <div class="stat-card accent"><div class="stat-value">${stats.total_files || 0}</div><div class="stat-label">Total Files</div></div>
                <div class="stat-card"><div class="stat-value">${formatBytes(stats.total_size_bytes || 0)}</div><div class="stat-label">Total Size</div></div>
            </div>
            <div class="search-bar">
                <input class="form-control" id="storage-search" placeholder="Search files..." onkeyup="if(event.key==='Enter')searchStorage()">
                <select class="form-control" id="storage-category" style="max-width:150px">
                    <option value="">All Categories</option>
                    <option value="input">Input</option>
                    <option value="output">Output</option>
                    <option value="artifact">Artifact</option>
                    <option value="intermediate">Intermediate</option>
                </select>
                <button class="btn btn-secondary" onclick="searchStorage()">Search</button>
            </div>
            <div id="storage-results">
                ${renderTable(['Filename', 'Type', 'Size', 'Category', 'Created', 'Actions'],
                    (files || []).map(f => [
                        f.filename,
                        `<span class="badge badge-info">${f.mime_type.split('/')[0] || 'file'}</span>`,
                        formatBytes(f.file_size),
                        `<span class="badge badge-neutral">${f.category}</span>`,
                        formatDate(f.created_at),
                        `<div class="btn-group">
                            <a href="/api/v1/storage/files/${f.id}/download" class="btn btn-sm btn-secondary" target="_blank">Download</a>
                            <button class="btn btn-sm btn-danger" onclick="deleteFile('${f.id}')">Delete</button>
                        </div>`
                    ])
                )}
            </div>
        `;
    } catch(e) {
        content.innerHTML = `<div class="page-header"><h1>Storage</h1></div>${emptyState('Storage not available', e.message)}`;
    }
}

async function searchStorage() {
    const query = document.getElementById('storage-search').value;
    const category = document.getElementById('storage-category').value;
    const params = {};
    if (query) params.query = query;
    if (category) params.category = category;
    try {
        const files = await api.storage.list(params);
        document.getElementById('storage-results').innerHTML = renderTable(
            ['Filename', 'Type', 'Size', 'Category', 'Created', 'Actions'],
            (files || []).map(f => [
                f.filename,
                `<span class="badge badge-info">${f.mime_type.split('/')[0] || 'file'}</span>`,
                formatBytes(f.file_size),
                `<span class="badge badge-neutral">${f.category}</span>`,
                formatDate(f.created_at),
                `<div class="btn-group">
                    <a href="/api/v1/storage/files/${f.id}/download" class="btn btn-sm btn-secondary" target="_blank">Download</a>
                    <button class="btn btn-sm btn-danger" onclick="deleteFile('${f.id}')">Delete</button>
                </div>`
            ])
        );
    } catch(e) { toast(e.message, 'error'); }
}

async function deleteFile(id) {
    if (!confirm('Delete this file?')) return;
    try { await api.storage.del(id); toast('File deleted', 'success'); renderStorage(); } catch(e) { toast(e.message, 'error'); }
}

function showIngestForm() {
    openModal('Ingest File', `
        <div class="form-group"><label>File Path</label><input class="form-control" id="ingest-path" placeholder="/path/to/file.mp4"></div>
        <div class="form-group"><label>Category</label>
            <select class="form-control" id="ingest-category"><option value="input">Input</option><option value="output">Output</option><option value="artifact">Artifact</option></select>
        </div>
        <div class="form-group"><label>Tags (comma-sep)</label><input class="form-control" id="ingest-tags" placeholder="video, raw"></div>
        <button class="btn btn-primary" onclick="doIngest()">Ingest</button>
    `);
}
async function doIngest() {
    try {
        const tags = (document.getElementById('ingest-tags').value || '').split(',').map(s => s.trim()).filter(Boolean);
        await api.storage.ingest({
            path: document.getElementById('ingest-path').value,
            category: document.getElementById('ingest-category').value,
            tags,
        });
        closeModal(); toast('File ingested', 'success'); renderStorage();
    } catch(e) { toast(e.message, 'error'); }
}

// =========================================================================
// QUEUES
// =========================================================================
async function renderQueues() {
    const content = document.getElementById('content');
    content.innerHTML = loading();
    try {
        const queues = await api.queues.list();
        content.innerHTML = `
            <div class="page-header">
                <h1>Queues</h1>
                <button class="btn btn-primary" onclick="showCreateQueueForm()">+ Create Queue</button>
            </div>
            ${renderTable(['Name', 'Type', 'Workers', 'Status', 'Actions'],
                queues.map(q => [
                    q.name,
                    `<span class="badge badge-info">${q.type}</span>`,
                    q.workers,
                    statusBadge(q.status),
                    `<div class="btn-group">
                        ${q.status === 'active' ? `<button class="btn btn-sm btn-secondary" onclick="pauseQueue('${q.name}')">Pause</button>` : ''}
                        ${q.status === 'paused' ? `<button class="btn btn-sm btn-primary" onclick="resumeQueue('${q.name}')">Resume</button>` : ''}
                        <button class="btn btn-sm btn-danger" onclick="deleteQueue('${q.name}')">Delete</button>
                    </div>`
                ])
            )}
        `;
    } catch(e) {
        content.innerHTML = `<div class="page-header"><h1>Queues</h1></div>${emptyState('Could not load queues', e.message)}`;
    }
}

function showCreateQueueForm() {
    openModal('Create Queue', `
        <div class="form-group"><label>Name</label><input class="form-control" id="q-name"></div>
        <div class="form-row">
            <div class="form-group"><label>Type</label><select class="form-control" id="q-type"><option>priority</option><option>fifo</option><option>parallel</option></select></div>
            <div class="form-group"><label>Workers</label><input class="form-control" id="q-workers" type="number" value="2"></div>
        </div>
        <button class="btn btn-primary" onclick="doCreateQueue()">Create</button>
    `);
}
async function doCreateQueue() {
    try {
        await api.queues.create({ name: document.getElementById('q-name').value, type: document.getElementById('q-type').value, workers: parseInt(document.getElementById('q-workers').value) });
        closeModal(); toast('Queue created', 'success'); renderQueues();
    } catch(e) { toast(e.message, 'error'); }
}
async function pauseQueue(name) { try { await api.queues.pause(name); toast('Queue paused', 'success'); renderQueues(); } catch(e) { toast(e.message, 'error'); } }
async function resumeQueue(name) { try { await api.queues.resume(name); toast('Queue resumed', 'success'); renderQueues(); } catch(e) { toast(e.message, 'error'); } }
async function deleteQueue(name) { if (!confirm(`Delete queue ${name}?`)) return; try { await api.queues.del(name); toast('Queue deleted', 'success'); renderQueues(); } catch(e) { toast(e.message, 'error'); } }

// =========================================================================
// LLM COMPILE
// =========================================================================
async function renderCompile() {
    const content = document.getElementById('content');
    content.innerHTML = `
        <div class="page-header"><h1>LLM Pipeline Compiler</h1></div>
        <div class="card">
            <p class="text-sm text-muted mb-16">Describe your workflow in natural language and the LLM will compile it into an executable pipeline.</p>
            <div class="form-group"><label>Workflow Description</label>
                <textarea class="form-control" id="compile-desc" rows="6" placeholder="Create a queue of video editing that first changes the resolution of the video found in /example/video.mp4 into 720p using ffmpeg, then find the cuts using scene detection, then launch facedetection.py on each clip..."></textarea>
            </div>
            <div class="form-group"><label>Input Variables (JSON)</label>
                <input class="form-control" id="compile-inputs" placeholder='{"video_path": "/path/to/video.mp4"}' value="{}">
            </div>
            <button class="btn btn-primary" id="compile-btn" onclick="doCompile()">Compile Pipeline</button>
        </div>
        <div id="compile-result"></div>
    `;
}

async function doCompile() {
    const btn = document.getElementById('compile-btn');
    btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Compiling...';
    try {
        const desc = document.getElementById('compile-desc').value;
        const inputs = JSON.parse(document.getElementById('compile-inputs').value || '{}');
        const result = await api.pipelines.compile(desc, inputs);
        document.getElementById('compile-result').innerHTML = `
            <div class="card">
                <div class="card-header"><h3>Compiled: ${result.name}</h3>${statusBadge(result.status)}</div>
                <p class="text-sm">Pipeline ID: <code class="mono">${result.id}</code></p>
                <div class="step-list mt-16">
                    ${(result.steps || []).map(s => `
                        <div class="step-item pending">
                            <span class="step-order">#${s.order + 1}</span>
                            <span class="step-name">${s.name}</span>
                            <span class="step-type">${s.command_type}</span>
                        </div>
                    `).join('')}
                </div>
                <div class="mt-16">
                    <button class="btn btn-primary" onclick="viewPipeline('${result.id}')">View Pipeline</button>
                </div>
            </div>
        `;
        toast('Pipeline compiled successfully', 'success');
    } catch(e) {
        document.getElementById('compile-result').innerHTML = `<div class="card"><p style="color:var(--danger)">${e.message}</p></div>`;
        toast(e.message, 'error');
    }
    btn.disabled = false; btn.textContent = 'Compile Pipeline';
}

// =========================================================================
// MONITORING
// =========================================================================
async function renderMonitoring() {
    const content = document.getElementById('content');
    content.innerHTML = loading();
    try {
        let health = {}, status = {};
        try { health = await api.system.health(); } catch(e) { health = { status: 'unavailable' }; }
        try { status = await api.system.status(); } catch(e) {}

        content.innerHTML = `
            <div class="page-header"><h1>Monitoring</h1>
                <button class="btn btn-secondary" onclick="renderMonitoring()">Refresh</button>
            </div>
            <div class="stats-grid">
                <div class="stat-card"><div class="stat-value">${statusBadge(health.status || 'unknown')}</div><div class="stat-label">Health</div></div>
                <div class="stat-card"><div class="stat-value">${status.uptime || '-'}</div><div class="stat-label">Uptime</div></div>
                <div class="stat-card"><div class="stat-value">${status.state || '-'}</div><div class="stat-label">State</div></div>
            </div>
            <div class="card">
                <div class="card-header"><h3>System Status</h3></div>
                <pre class="mono" style="padding:12px;background:var(--bg-input);border-radius:6px;max-height:400px;overflow:auto">${JSON.stringify(status, null, 2)}</pre>
            </div>
        `;
    } catch(e) {
        content.innerHTML = `<div class="page-header"><h1>Monitoring</h1></div>${emptyState('Backend not available', e.message)}`;
    }
}

// =========================================================================
// SETTINGS
// =========================================================================
async function renderSettings() {
    const content = document.getElementById('content');
    content.innerHTML = `
        <div class="page-header"><h1>Settings</h1></div>
        <div class="card">
            <h3 style="margin-bottom:16px">LLM Provider</h3>
            <div class="form-row">
                <div class="form-group"><label>Provider</label>
                    <select class="form-control" id="set-provider">
                        <option>openai</option><option>anthropic</option><option>gemini</option><option>lmstudio</option>
                    </select>
                </div>
                <div class="form-group"><label>Model</label><input class="form-control" id="set-model" value="gpt-4"></div>
            </div>
            <div class="form-group"><label>API Key</label><input class="form-control" id="set-apikey" type="password" placeholder="sk-..."></div>
            <div class="form-group"><label>Base URL (LMStudio)</label><input class="form-control" id="set-baseurl" placeholder="http://localhost:1234/v1"></div>
            <p class="text-sm text-muted mt-8">Settings are configured via YAML config files. This panel shows the available options.</p>
        </div>
        <div class="card">
            <h3 style="margin-bottom:16px">Import / Export Programs</h3>
            <div class="btn-group">
                <button class="btn btn-secondary" onclick="exportPrograms()">Export All Programs</button>
                <button class="btn btn-secondary" onclick="showImportForm()">Import Programs</button>
            </div>
        </div>
    `;
}

async function exportPrograms() {
    try {
        const data = await api.programs.exportPrograms();
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = 'programs-export.json'; a.click();
        URL.revokeObjectURL(url);
        toast('Programs exported', 'success');
    } catch(e) { toast(e.message, 'error'); }
}

function showImportForm() {
    openModal('Import Programs', `
        <div class="form-group"><label>Paste JSON (array of program definitions)</label>
            <textarea class="form-control" id="import-json" rows="10" placeholder='[{"name": "...", "command_type": "python", ...}]'></textarea>
        </div>
        <button class="btn btn-primary" onclick="doImport()">Import</button>
    `);
}
async function doImport() {
    try {
        const programs = JSON.parse(document.getElementById('import-json').value);
        await api.programs.importPrograms(programs);
        closeModal(); toast('Programs imported', 'success');
    } catch(e) { toast(e.message, 'error'); }
}
