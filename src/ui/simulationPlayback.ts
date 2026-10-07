import * as THREE from 'three';
import type { AquaSimModel } from '@/parser/types';
import { assertResultMatchesModel, parseWinchResult, type WinchResult } from '@/simulation/resultFormat';
import type { AquaSimViewer } from '@/viewer/AquaSimViewer';

export function setupSimulationPlayback(
    viewer: AquaSimViewer,
    getModel: () => AquaSimModel | null
): { setModel: (model: AquaSimModel) => void } {
    const fileInput = document.getElementById('simulation-result-input') as HTMLInputElement;
    const openButton = document.getElementById('btn-open-simulation-result') as HTMLButtonElement;
    const playButton = document.getElementById('btn-simulation-play') as HTMLButtonElement;
    const timeline = document.getElementById('simulation-timeline') as HTMLInputElement;
    const timeLabel = document.getElementById('simulation-time') as HTMLElement;
    const speedSelect = document.getElementById('simulation-speed') as HTMLSelectElement;

    const panel = document.getElementById('simulation-panel') as HTMLElement;
    const toggleButton = document.getElementById('btn-toggle-simulation') as HTMLButtonElement;
    const playbackControls = document.getElementById('simulation-controls') as HTMLElement;
    toggleButton.addEventListener('click', () => {
        const collapsed = !playbackControls.hidden;
        playbackControls.hidden = collapsed;
        panel.classList.toggle('is-collapsed', collapsed);
        toggleButton.textContent = collapsed ? 'Show winch simulation' : 'Hide';
        toggleButton.setAttribute('aria-expanded', String(!collapsed));
    });

    let result: WinchResult | null = null;
    let currentIndex = 0;
    let playbackTimeS = 0;
    let maximumResultTensionN = 0;
    let animationFrame: number | null = null;
    let lastTick = 0;

    function formatTime(seconds: number): string {
        const minutes = Math.floor(seconds / 60);
        const remainder = Math.floor(seconds % 60);
        return `${minutes}:${String(remainder).padStart(2, '0')}`;
    }

    function renderAtTime(timeS: number): void {
        if (!result) return;
        const finalFrame = result.frames[result.frames.length - 1];
        playbackTimeS = Math.max(0, Math.min(finalFrame.timeS, timeS));
        let upperIndex = 1;
        while (upperIndex < result.frames.length && result.frames[upperIndex].timeS < playbackTimeS) upperIndex++;
        const lowerIndex = Math.min(result.frames.length - 1, upperIndex - 1);
        const lower = result.frames[lowerIndex];
        const upper = result.frames[Math.min(result.frames.length - 1, upperIndex)];
        const interval = upper.timeS - lower.timeS;
        const fraction = interval > 0 ? (playbackTimeS - lower.timeS) / interval : 0;
        const interpolate = (a: number, b: number) => a + (b - a) * fraction;
        currentIndex = fraction < 0.5 ? lowerIndex : Math.min(result.frames.length - 1, upperIndex);
        const nodePositions = new Map<number, THREE.Vector3>();
        result.header.nodeIds.forEach((nodeId, nodeIndex) => {
            const offset = nodeIndex * 3;
            nodePositions.set(nodeId, new THREE.Vector3(
                interpolate(lower.nodePositionsM[offset], upper.nodePositionsM[offset]),
                interpolate(lower.nodePositionsM[offset + 1], upper.nodePositionsM[offset + 1]),
                interpolate(lower.nodePositionsM[offset + 2], upper.nodePositionsM[offset + 2])
            ));
        });
        const tensionByElement = new Map<string, number>();
        result.header.ropeElements.forEach((element, elementIndex) => {
            tensionByElement.set(
                `${element.componentId}:${element.elementId}`,
                interpolate(lower.ropeTensionsN[elementIndex], upper.ropeTensionsN[elementIndex])
            );
        });
        viewer.applyWinchFrame(nodePositions, tensionByElement, maximumResultTensionN);
        timeline.value = String(currentIndex);
        timeLabel.textContent = `${formatTime(playbackTimeS)} / ${formatTime(finalFrame.timeS)}`;
    }

    function applyFrame(index: number): void {
        if (!result) return;
        const frameIndex = Math.max(0, Math.min(result.frames.length - 1, index));
        renderAtTime(result.frames[frameIndex].timeS);
    }

    function stop(): void {
        if (animationFrame !== null) cancelAnimationFrame(animationFrame);
        animationFrame = null;
        lastTick = 0;
        playButton.textContent = 'Play';
        playButton.setAttribute('aria-pressed', 'false');
    }

    function tick(timestamp: number): void {
        if (!result) return;
        if (lastTick === 0) lastTick = timestamp;
        const elapsed = (timestamp - lastTick) / 1000;
        lastTick = timestamp;
        const playbackSpeed = Number(speedSelect.value);
        renderAtTime(playbackTimeS + elapsed * playbackSpeed);
        if (playbackTimeS >= result.frames[result.frames.length - 1].timeS) {
            stop();
            return;
        }
        animationFrame = requestAnimationFrame(tick);
    }

    openButton.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', async () => {
        const file = fileInput.files?.[0];
        fileInput.value = '';
        if (!file) return;
        stop();
        try {
            const parsed = parseWinchResult(await file.arrayBuffer());
            const model = getModel();
            if (!model) throw new Error('Load the matching .amodel file before opening simulation results.');
            assertResultMatchesModel(parsed, model.name || 'AquaSim Model', [...model.nodes.values()]);
            result = parsed;
            currentIndex = 0;
            playbackTimeS = 0;
            maximumResultTensionN = 0;
            for (const frame of parsed.frames) {
                for (const tension of frame.ropeTensionsN) {
                    maximumResultTensionN = Math.max(maximumResultTensionN, tension);
                }
            }
            timeline.min = '0';
            timeline.max = String(parsed.frames.length - 1);
            timeline.step = '1';
            timeline.disabled = false;
            playButton.disabled = false;
            applyFrame(0);
        } catch (error) {
            result = null;
            viewer.clearWinchFrame();
            alert(error instanceof Error ? error.message : String(error));
            timeline.disabled = true;
            playButton.disabled = true;
        }
    });

    timeline.addEventListener('input', () => {
        stop();
        applyFrame(Number(timeline.value));
    });
    playButton.addEventListener('click', () => {
        if (!result) return;
        if (animationFrame !== null) {
            stop();
            return;
        }
        if (currentIndex >= result.frames.length - 1) applyFrame(0);
        playButton.textContent = 'Pause';
        playButton.setAttribute('aria-pressed', 'true');
        animationFrame = requestAnimationFrame(tick);
    });


    return {
        setModel(_model: AquaSimModel): void {
            stop();
            result = null;
            viewer.clearWinchFrame();
            timeline.disabled = true;
            playButton.disabled = true;
            timeline.value = '0';
            timeLabel.textContent = '0:00 / 0:00';
        }
    };
}
