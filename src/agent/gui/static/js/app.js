/* App initialization and navigation router */

let currentPage = 'dashboard';

const pageRenderers = {
    dashboard: renderDashboard,
    pipelines: renderPipelines,
    templates: renderTemplates,
    programs: renderPrograms,
    storage: renderStorage,
    queues: renderQueues,
    compile: renderCompile,
    monitoring: renderMonitoring,
    settings: renderSettings,
};

function navigateTo(page) {
    if (!pageRenderers[page]) {
        toast(`Unknown page: ${page}`, 'error');
        return;
    }

    currentPage = page;

    // Update active nav item
    document.querySelectorAll('.nav-item').forEach(el => {
        el.classList.toggle('active', el.dataset.page === page);
    });

    // Update URL hash
    window.location.hash = page;

    // Render the page
    pageRenderers[page]();
}

// Handle browser back/forward
window.addEventListener('hashchange', () => {
    const page = window.location.hash.replace('#', '') || 'dashboard';
    if (page !== currentPage && pageRenderers[page]) {
        navigateTo(page);
    }
});

// Init on page load
document.addEventListener('DOMContentLoaded', () => {
    const page = window.location.hash.replace('#', '') || 'dashboard';
    navigateTo(page);
});
