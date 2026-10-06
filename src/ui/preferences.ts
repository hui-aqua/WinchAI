import type { AquaSimViewer } from '@/viewer/AquaSimViewer';

/** Apply persisted theme and initialize the responsive sidebar. */
export function setupPreferences(viewer: AquaSimViewer): void {
    const themeButton = document.getElementById('btn-toggle-theme') as HTMLButtonElement;
    let theme: 'light' | 'dark' = 'dark';
    try {
        theme = localStorage.getItem('amodel-theme') === 'light' ? 'light' : 'dark';
    } catch {
        // Storage may be unavailable in private or restricted browser contexts.
    }
    const applyTheme = () => {
        document.documentElement.dataset.theme = theme;
        viewer.setTheme(theme);
        themeButton.title = 'Switch to ' + (theme === 'dark' ? 'light' : 'dark') + ' theme';
        themeButton.setAttribute('aria-label', 'Switch to ' + (theme === 'dark' ? 'light' : 'dark') + ' theme');
    };
    applyTheme();
    themeButton.addEventListener('click', () => {
        theme = theme === 'dark' ? 'light' : 'dark';
        applyTheme();
        try {
            localStorage.setItem('amodel-theme', theme);
        } catch {
            // Theme switching still works without persistence.
        }
    });
    const sidebarButton = document.getElementById('btn-toggle-sidebar') as HTMLButtonElement;
    const sidebar = document.getElementById('model-sidebar') as HTMLElement;
    function setSidebarCollapsed(collapsed: boolean) {
        sidebar.hidden = collapsed;
        sidebarButton.classList.toggle('collapsed', collapsed);
        sidebarButton.title = collapsed ? 'Show model components' : 'Hide model components';
        sidebarButton.setAttribute('aria-label', sidebarButton.title);
        sidebarButton.setAttribute('aria-expanded', String(!collapsed));
    }
    setSidebarCollapsed(window.matchMedia('(max-width: 600px)').matches);
    sidebarButton.addEventListener('click', () => setSidebarCollapsed(!sidebar.hidden));

}
