import * as THREE from 'three';
import { findPlaybackInterval } from '@/simulation/playbackMath';
import type { AquaSimModel } from '@/parser/types';
import { assertResultMatchesModel, parseWinchResult, type WinchResult, type WinchResultFrame } from '@/simulation/resultFormat';
import type { AquaSimViewer } from '@/viewer/AquaSimViewer';

export function setupSimulationPlayback(
    viewer: AquaSimViewer,
    getModel: () => AquaSimModel | null
): { setModel: (model: AquaSimModel) => void; loadResult: (buffer: ArrayBuffer) => void } {
    const fileInput = document.getElementById('simulation-result-input') as HTMLInputElement;
    const openButton = document.getElementById('btn-open-simulation-result') as HTMLButtonElement;
    const playButton = document.getElementById('btn-simulation-play') as HTMLButtonElement;
    const timeline = document.getElementById('simulation-timeline') as HTMLInputElement;
    const timeLabel = document.getElementById('simulation-time') as HTMLElement;
    const speedSelect = document.getElementById('simulation-speed') as HTMLSelectElement;
    const status = document.getElementById('simulation-status') as HTMLElement;
    const warnings = document.getElementById('simulation-warnings') as HTMLElement;

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
        const { lower: lowerIndex, upper: upperIndex, fraction } = findPlaybackInterval(result.frames, playbackTimeS);
        const lower = result.frames[lowerIndex];
        const upper = result.frames[upperIndex];
        const interpolate = (a: number, b: number) => a + (b - a) * fraction;
        const frame: WinchResultFrame = {
            timeS: playbackTimeS,
            takeUpM: interpolate(lower.takeUpM, upper.takeUpM),
            translationZM: interpolate(lower.translationZM, upper.translationZM),
            winch1TensionN: interpolate(lower.winch1TensionN, upper.winch1TensionN),
            winch2TensionN: interpolate(lower.winch2TensionN, upper.winch2TensionN),
            netVerticalLoadN: interpolate(lower.netVerticalLoadN, upper.netVerticalLoadN),
            momentXNm: interpolate(lower.momentXNm, upper.momentXNm),
            momentYNm: interpolate(lower.momentYNm, upper.momentYNm),
            momentZNm: interpolate(lower.momentZNm, upper.momentZNm),
            nodePositionsM: lower.nodePositionsM,
            ropeTensionsN: lower.ropeTensionsN
        };
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
        const schedule = result.header.settings.winchPayoutsM;
        const progress = finalFrame.timeS > 0 ? playbackTimeS / finalFrame.timeS : 0;
        const payoutLabel = schedule
            ? result.header.winches.map(winch => `${winch.name}: ${((schedule[String(winch.componentId)] ?? 0) * progress).toFixed(2)} m payout`).join(' · ')
            : `${frame.takeUpM >= 0 ? 'retrieved' : 'paid out'} ${Math.abs(frame.takeUpM).toFixed(2)} m`;
        timeLabel.textContent = `${formatTime(playbackTimeS)} / ${formatTime(finalFrame.timeS)} · ${payoutLabel} · mean vertical movement ${frame.translationZM.toFixed(2)} m`;
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
        try {
            loadResult(await file.arrayBuffer());
        } catch (error) {
            status.textContent = error instanceof Error ? error.message : String(error);
        }
    });

    function loadResult(buffer: ArrayBuffer): void {
        stop();
        try {
            const parsed = parseWinchResult(buffer);
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
            status.textContent = `${parsed.header.modelName} · ${parsed.frames.length} frames · ${parsed.header.ropeElements.length} cable elements · ${parsed.header.settings.solverMethod === 'quasi-static' ? (parsed.header.settings.equilibriumStrategy === 'time-marching' ? 'implicit time marching' : parsed.header.settings.equilibriumStrategy === 'parallel-rope-length' ? 'parallel quasi-static equilibria' : 'quasi-static equilibria') : `${parsed.header.settings.takeUpSpeedMPerMin} m/min take-up`}`;
            warnings.replaceChildren();
            for (const message of parsed.header.warnings) {
                const item = document.createElement('li');
                item.textContent = message;
                warnings.append(item);
            }
            for (const assumption of parsed.header.assumptions) {
                const item = document.createElement('li');
                item.textContent = `Assumption: ${assumption}`;
                warnings.append(item);
            }
            warnings.hidden = warnings.childElementCount === 0;
            applyFrame(0);
        } catch (error) {
            result = null;
            viewer.clearWinchFrame();
            status.textContent = error instanceof Error ? error.message : String(error);
            timeline.disabled = true;
            playButton.disabled = true;
            warnings.replaceChildren();
            warnings.hidden = true;
            throw error;
        }
    }

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
        loadResult,
        setModel(model: AquaSimModel): void {
            stop();
            result = null;
            viewer.clearWinchFrame();
            timeline.disabled = true;
            playButton.disabled = true;
            timeline.value = '0';
            timeLabel.textContent = 'Load an .aqwr result generated by the local Python solver.';
            status.textContent = `Loaded ${model.name || 'AquaSim Model'}. Run the local solver, then open its .aqwr result.`;
            warnings.replaceChildren();
            warnings.hidden = true;
        }
    };
}
