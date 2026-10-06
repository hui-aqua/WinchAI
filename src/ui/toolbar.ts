import type { AquaSimViewer } from '@/viewer/AquaSimViewer';

/** Connect camera, display, and file selection controls to the viewer. */
export function setupToolbar(viewer: AquaSimViewer, fileInput: HTMLInputElement): void {
    const btnOpenFile = document.getElementById('btn-open-file') as HTMLButtonElement;
    const btnFitView = document.getElementById('btn-fit-view') as HTMLButtonElement;
    const btnFitCage = document.getElementById('btn-fit-cage') as HTMLButtonElement;
    const btnToggleCamera = document.getElementById('btn-toggle-camera') as HTMLButtonElement | null;
    const selectRenderMode = document.getElementById('select-render-mode') as HTMLSelectElement | null;
    const btnToggleGrid = document.getElementById('btn-toggle-grid') as HTMLButtonElement;
    const btnToggleAxes = document.getElementById('btn-toggle-axes') as HTMLButtonElement;

    // View Orientation Buttons
    const btnViewX = document.getElementById('btn-view-x') as HTMLButtonElement | null;
    const btnViewY = document.getElementById('btn-view-y') as HTMLButtonElement | null;
    const btnViewZ = document.getElementById('btn-view-z') as HTMLButtonElement | null;
    const btnViewIso = document.getElementById('btn-view-iso') as HTMLButtonElement | null;

    let isGridVisible = false;
    let isAxesVisible = false;

    // Toolbar Actions
    btnOpenFile.addEventListener('click', () => {
        fileInput.click();
    });

    btnFitView.addEventListener('click', () => {
        viewer.fitView();
    });

    btnFitCage.addEventListener('click', () => {
        viewer.fitCageView();
    });

    // Camera Projection Switcher (Perspective <-> Orthographic)
    function updateCameraButtons(mode: 'perspective' | 'orthographic') {
        for (const button of [btnToggleCamera]) {
            if (!button) continue;
            button.setAttribute('aria-checked', String(mode === 'orthographic'));
            button.title = mode === 'perspective' ? 'Perspective projection ? switch to Orthographic' : 'Orthographic projection ? switch to Perspective';
        }
    }
    updateCameraButtons(viewer.getCameraMode());

    function handleToggleCamera() {
        const newMode = viewer.toggleCameraMode();
        updateCameraButtons(newMode);
    }

    if (btnToggleCamera) {
        btnToggleCamera.addEventListener('click', handleToggleCamera);
    }
    // Quick View Alignment Buttons (X+, Y+, Z+, Iso)
    if (btnViewX) {
        btnViewX.addEventListener('click', () => viewer.alignView('x+'));
    }
    if (btnViewY) {
        btnViewY.addEventListener('click', () => viewer.alignView('y+'));
    }
    if (btnViewZ) {
        btnViewZ.addEventListener('click', () => viewer.alignView('z+'));
    }
    if (btnViewIso) {
        btnViewIso.addEventListener('click', () => viewer.alignView('iso'));
    }

    // Render Mode (Solid / Wireframe / Both)
    if (selectRenderMode) {
        selectRenderMode.addEventListener('change', () => {
            viewer.setRenderMode(selectRenderMode.value as 'solid' | 'wireframe' | 'both');
        });
    }

    // Master Grid & Axes Toggles
    btnToggleGrid.addEventListener('click', () => {
        isGridVisible = !isGridVisible;
        viewer.setGridVisibility(isGridVisible);
        btnToggleGrid.classList.toggle('btn-active', isGridVisible);
        btnToggleGrid.setAttribute('aria-pressed', String(isGridVisible));
    });

    btnToggleAxes.addEventListener('click', () => {
        isAxesVisible = !isAxesVisible;
        viewer.setAxesVisibility(isAxesVisible);
        btnToggleAxes.classList.toggle('btn-active', isAxesVisible);
        btnToggleAxes.setAttribute('aria-pressed', String(isAxesVisible));
    });
}
