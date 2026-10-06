import { setupToolbar } from '@/ui/toolbar';
import { setupPreferences } from '@/ui/preferences';
import { AquaSimViewer } from '@/viewer/AquaSimViewer';
import { setupFileLoader } from '@/ui/fileLoader';
import { ModelTreeUI } from '@/ui/modelTree';
import type { AquaSimModel } from '@/parser/types';
import { setupSimulationPlayback } from '@/ui/simulationPlayback';

export function initializeApp(): void {
    // DOM Elements
    const viewportContainer = document.getElementById('viewport-container') as HTMLElement;
    const fileInput = document.getElementById('file-input') as HTMLInputElement;
    const treeContainer = document.getElementById('model-tree') as HTMLElement;
    const statsContainer = document.getElementById('model-stats') as HTMLElement;
    const loadingSpinner = document.getElementById('loading-spinner') as HTMLElement;
    const loadingText = document.getElementById('loading-text') as HTMLElement;
    const dropOverlay = document.getElementById('drop-overlay') as HTMLElement;

    // Initialize 3D Viewer
    const viewer = new AquaSimViewer(viewportContainer);
    let currentModel: AquaSimModel | null = null;
    const simulationPlayback = setupSimulationPlayback(viewer, () => currentModel);

    setupPreferences(viewer);

    // Initialize Model Tree UI
    const modelTree = new ModelTreeUI(
        {
            treeContainer,
            statsContainer
        },
        viewer
    );

    function showLoading(msg: string) {
        loadingText.textContent = msg;
        loadingSpinner.style.display = 'flex';
    }

    function hideLoading() {
        loadingSpinner.style.display = 'none';
    }

    function handleModelLoaded(model: AquaSimModel, filename: string) {
        hideLoading();
        currentModel = model;

        // Load into 3D scene
        viewer.loadModel(model);
        simulationPlayback.setModel(model);
        dropOverlay.classList.remove('empty-state-visible');

        // Update hierarchy and statistics in UI
        modelTree.update(model, filename);

        if (window.location.search.includes('focus=cage')) {
            viewer.fitCageView();
        }

        // Notify if warnings occurred
        if (model.report.warnings.length > 0) {
            console.warn(`Model loaded with ${model.report.warnings.length} warning(s):`, model.report.warnings);
        }
    }

    function handleModelError(error: Error, filename: string) {
        hideLoading();
        alert(`Failed to load model "${filename}":\n\n${error.message}`);
        console.error(error);
    }

    // Setup File Loading (Input + Drag & Drop)
    setupFileLoader(fileInput, viewportContainer, {
        onLoadStart: (filename) => {
            showLoading(`Parsing "${filename}"...`);
        },
        onLoadSuccess: handleModelLoaded,
        onLoadError: handleModelError
    });

    setupToolbar(viewer, fileInput);
}
