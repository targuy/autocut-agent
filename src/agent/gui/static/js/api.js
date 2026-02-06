/* API client for AutoCut Agent */

const API_BASE = '/api/v1';

const api = {
    async request(method, path, body = null) {
        const opts = {
            method,
            headers: { 'Content-Type': 'application/json' },
        };
        if (body) opts.body = JSON.stringify(body);
        const resp = await fetch(`${API_BASE}${path}`, opts);
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText }));
            throw new Error(err.detail || `HTTP ${resp.status}`);
        }
        if (resp.status === 204) return null;
        return resp.json();
    },

    get(path) { return this.request('GET', path); },
    post(path, body) { return this.request('POST', path, body); },
    put(path, body) { return this.request('PUT', path, body); },
    del(path) { return this.request('DELETE', path); },

    // Pipelines
    pipelines: {
        list: () => api.get('/pipelines'),
        get: (id) => api.get(`/pipelines/${id}`),
        compile: (desc, inputs) => api.post('/pipelines/compile', { description: desc, inputs }),
        create: (templateId, inputs) => api.post('/pipelines', { template_id: templateId, inputs }),
        resume: (id) => api.post(`/pipelines/${id}/resume`),
        cancel: (id) => api.post(`/pipelines/${id}/cancel`),
        artifacts: (id) => api.get(`/pipelines/${id}/artifacts`),
        skipStep: (pid, sid) => api.post(`/pipelines/${pid}/steps/${sid}/skip`),
    },

    // Templates
    templates: {
        list: () => api.get('/templates'),
        get: (id) => api.get(`/templates/${id}`),
        create: (data) => api.post('/templates', data),
        update: (id, data) => api.put(`/templates/${id}`, data),
        del: (id) => api.del(`/templates/${id}`),
        clone: (id, inputs) => api.post(`/templates/${id}/clone`, { inputs }),
    },

    // Programs
    programs: {
        list: (activeOnly = true) => api.get(`/programs?active_only=${activeOnly}`),
        get: (name) => api.get(`/programs/${encodeURIComponent(name)}`),
        register: (data) => api.post('/programs', data),
        update: (name, data) => api.put(`/programs/${encodeURIComponent(name)}`, data),
        updateParams: (name, params) => api.put(`/programs/${encodeURIComponent(name)}/parameters`, { parameters: params }),
        addParam: (name, param) => api.post(`/programs/${encodeURIComponent(name)}/parameters`, param),
        deleteParam: (name, paramName) => api.del(`/programs/${encodeURIComponent(name)}/parameters/${encodeURIComponent(paramName)}`),
        deactivate: (name) => api.del(`/programs/${encodeURIComponent(name)}`),
        stats: (name) => api.get(`/programs/${encodeURIComponent(name)}/stats`),
        advisories: (name) => api.get(`/programs/${encodeURIComponent(name)}/advisories`),
        paramSets: (name) => api.get(`/programs/${encodeURIComponent(name)}/param-sets`),
        injectScore: (name, data) => api.post(`/programs/${encodeURIComponent(name)}/scores`, data),
        injectBatch: (name, scores) => api.post(`/programs/${encodeURIComponent(name)}/scores/batch`, { scores }),
        clearScores: (name) => api.del(`/programs/${encodeURIComponent(name)}/scores`),
        importPrograms: (programs) => api.post('/programs/import', { programs }),
        exportPrograms: () => api.get('/programs/export'),
    },

    // Storage
    storage: {
        list: (params = {}) => {
            const q = new URLSearchParams(params).toString();
            return api.get(`/storage/files${q ? '?' + q : ''}`);
        },
        get: (id) => api.get(`/storage/files/${id}`),
        del: (id) => api.del(`/storage/files/${id}`),
        stats: () => api.get('/storage/stats'),
        pipelineFiles: (pid) => api.get(`/storage/pipelines/${pid}/files`),
        ingest: (data) => api.post('/storage/ingest', data),
        exportFile: (id, dest) => api.post(`/storage/files/${id}/export`, { destination: dest }),
    },

    // Queues
    queues: {
        list: () => api.get('/queues'),
        get: (name) => api.get(`/queues/${encodeURIComponent(name)}`),
        create: (data) => api.post('/queues', data),
        pause: (name) => api.post(`/queues/${encodeURIComponent(name)}/pause`),
        resume: (name) => api.post(`/queues/${encodeURIComponent(name)}/resume`),
        del: (name) => api.del(`/queues/${encodeURIComponent(name)}`),
    },

    // System
    system: {
        status: () => api.get('/status'),
        health: () => api.get('/health'),
        advisories: () => api.get('/advisories'),
    },
};
