import * as THREE from 'three';
import type { AquaSimModel } from '@/parser/types';
import { assertResultMatchesModel, parseWinchResult, type WinchResult, type WinchResultFrame } from '@/simulation/resultFormat';
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
    const status = document.getElementById('simulation-status') as HTMLElement;
    const plot = document.getElementById('simulation-force-plot') as HTMLCanvasElement;
    const plotLegend = document.getElementById('simulation-plot-legend') as HTMLElement;
    const warnings = document.getElementById('simulation-warnings') as HTMLElement;

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

    function drawPlot(frameIndex: number): void {
        const context = plot.getContext('2d');
        if (!context || !result) return;
        const width = plot.clientWidth || 500;
        const height = plot.clientHeight || 115;
        const pixelRatio = window.devicePixelRatio || 1;
        plot.width = Math.round(width * pixelRatio);
        plot.height = Math.round(height * pixelRatio);
        context.scale(pixelRatio, pixelRatio);
        context.clearRect(0, 0, width, height);

        const margin = { left: 54, right: 10, top: 8, bottom: 20 };
        const chartWidth = width - margin.left - margin.right;
        const chartHeight = height - margin.top - margin.bottom;
        const maximum = Math.max(1, ...result.frames.flatMap(frame => [
            frame.winch1TensionN, frame.winch2TensionN, Math.abs(frame.netVerticalLoadN)
        ]));
        context.font = '10px ui-monospace, monospace';
        context.strokeStyle = '#475569';
        context.fillStyle = '#94a3b8';
        context.lineWidth = 1;
        for (let tick = 0; tick <= 2; tick++) {
            const y = margin.top + chartHeight * tick / 2;
            context.beginPath();
            context.moveTo(margin.left, y);
            context.lineTo(width - margin.right, y);
            context.stroke();
            context.fillText(`${Math.round(maximum * (1 - tick / 2))}`, 2, y + 3);
        }

        const series: Array<{ key: keyof WinchResultFrame; color: string }> = [
            { key: 'winch1TensionN', color: '#38bdf8' },
            { key: 'winch2TensionN', color: '#f59e0b' },
            { key: 'netVerticalLoadN', color: '#34d399' }
        ];
        for (const line of series) {
            context.beginPath();
            context.strokeStyle = line.color;
            context.lineWidth = 1.7;
            result.frames.forEach((frame, index) => {
                const x = margin.left + (result!.frames.length < 2 ? 0 : chartWidth * index / (result!.frames.length - 1));
                const y = margin.top + chartHeight * (1 - Math.min(maximum, Math.abs(frame[line.key] as number)) / maximum);
                if (index === 0) context.moveTo(x, y);
                else context.lineTo(x, y);
            });
            context.stroke();
        }

        const cursorX = margin.left + (result.frames.length < 2 ? 0 : chartWidth * frameIndex / (result.frames.length - 1));
        context.beginPath();
        context.strokeStyle = '#f8fafc';
        context.lineWidth = 1;
        context.moveTo(cursorX, margin.top);
        context.lineTo(cursorX, margin.top + chartHeight);
        context.stroke();
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
        timeLabel.textContent = `${formatTime(playbackTimeS)} · take-up ${frame.takeUpM.toFixed(2)} m · ${frame.translationZM.toFixed(2)} m submerged lift`;
        drawPlot(currentIndex);
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
            status.textContent = `${parsed.header.modelName} · ${parsed.frames.length} frames · ${parsed.header.ropeElements.length} dynamic rope elements · ${parsed.header.settings.takeUpSpeedMPerMin} m/min take-up`;
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
            plotLegend.hidden = false;
            applyFrame(0);
        } catch (error) {
            result = null;
            viewer.clearWinchFrame();
            status.textContent = error instanceof Error ? error.message : String(error);
            timeline.disabled = true;
            playButton.disabled = true;
            warnings.replaceChildren();
            warnings.hidden = true;
            plotLegend.hidden = true;
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

    window.addEventListener('resize', () => drawPlot(currentIndex));

    return {
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
            plotLegend.hidden = true;
            drawPlot(0);
        }
    };
}
