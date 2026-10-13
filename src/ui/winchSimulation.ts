import { readModelProperties, readModelFile } from '@/simulation/modelProperties';
import type { setupSimulationPlayback } from '@/ui/simulationPlayback';
import type { ModelProperties, SimulationControls } from '@/simulation/controls';

type SimulationPlayback = ReturnType<typeof setupSimulationPlayback>;
const GRAVITY = 9.80665;
const display = (value: number): string => String(Number(value.toPrecision(12)));

export function setupWinchSimulation(playback: SimulationPlayback): { setModelFile: (file: File | null) => void } {
    const element = <T extends HTMLElement>(id: string): T => document.getElementById(id) as T;
    const propertiesButton = element<HTMLButtonElement>('btn-component-properties');
    const propertiesDialog = element<HTMLDialogElement>('component-properties-dialog');
    const propertiesForm = element<HTMLFormElement>('component-properties-form');
    const propertiesStatus = element('component-properties-status');
    const runButton = element<HTMLButtonElement>('btn-run-winch-simulation');
    const dialog = element<HTMLDialogElement>('winch-simulation-dialog');
    const form = element<HTMLFormElement>('winch-simulation-form');
    const status = element('winch-run-status');
    const diagnosticsButton = element<HTMLButtonElement>('btn-download-winch-diagnostics');
    let diagnostic: Record<string, unknown> | null = null;
    try { diagnostic = JSON.parse(localStorage.getItem('winchai-last-run') || 'null') as Record<string, unknown> | null; } catch { /* Storage unavailable. */ }
    diagnosticsButton.disabled = !diagnostic;
    function saveDiagnostic(updates: Record<string, unknown>): void {
        diagnostic = { ...diagnostic, ...updates, updatedAt: new Date().toISOString() };
        diagnosticsButton.disabled = false;
        try { localStorage.setItem('winchai-last-run', JSON.stringify(diagnostic)); } catch { /* Download remains available when storage is full. */ }
    }
    diagnosticsButton.addEventListener('click', () => {
        const blob = new Blob([JSON.stringify(diagnostic, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a'); link.href = url; link.download = 'winchai-run-diagnostics.json';
        link.click(); URL.revokeObjectURL(url);
    });
    const submit = element<HTMLButtonElement>('btn-submit-winch-simulation');
    const cancel = element<HTMLButtonElement>('btn-cancel-winch-simulation');
    const reset = element<HTMLButtonElement>('btn-reset-winch-properties');
    const components = element('winch-component-properties');
    const winches = element('winch-control-properties');
    const duration = element<HTMLInputElement>('winch-duration');
    const interval = element<HTMLInputElement>('winch-save-interval');
    let file: File | null = null;
    let revision = 0;
    let running = false;
    let modelXml: string | null = null;
    let properties: ModelProperties | null = null;

    function setField(field: HTMLInputElement, property: string, amount: number): void {
        const text = property === 'lengthM' ? amount.toFixed(2)
            : ['massKg', 'weightInAirN', 'weightInWaterN'].includes(property) ? amount.toFixed(0) : display(amount);
        field.value = text;
        field.dataset.displayValue = text;
        field.dataset.exactValue = String(amount);
    }
    function input(row: HTMLElement, id: number, property: string, value: number, readonly = false): HTMLInputElement {
        const cell = document.createElement('td');
        const field = document.createElement('input');
        field.type = 'number'; field.step = 'any'; field.required = true;
        setField(field, property, value); field.readOnly = readonly;
        field.dataset.componentId = String(id); field.dataset.property = property;
        field.setAttribute('aria-label', `Component ${id} ${property}`);
        if (property === 'massKg' || property === 'weightInAirN') field.min = '0';
        if (property === 'lengthM' || property === 'eaN') field.min = '0.000000001';
        cell.append(field); row.append(cell);
        return field;
    }
    function cell(row: HTMLElement, value: string): HTMLElement {
        const result = document.createElement('td'); result.textContent = value; row.append(result); return result;
    }
    function value(id: number, property: string): number {
        const field = document.querySelector<HTMLInputElement>(`input[data-component-id="${id}"][data-property="${property}"]`)!;
        const amount = field.value === field.dataset.displayValue ? Number(field.dataset.exactValue) : Number(field.value);
        return ['weightInAirN', 'weightInWaterN'].includes(property) ? amount * value(id, 'lengthM') : amount;
    }
    function updateWeightTotals(): void {
        if (!properties) return;
        const sums = new Map<string, {air: number; water: number}>();
        let airTotal = 0, waterTotal = 0;
        for (const component of properties.components) {
            const air = value(component.componentId, 'weightInAirN');
            const water = value(component.componentId, 'weightInWaterN');
            components.querySelector<HTMLElement>(`[data-total-air="${component.componentId}"]`)!.textContent = air.toFixed(0);
            components.querySelector<HTMLElement>(`[data-total-water="${component.componentId}"]`)!.textContent = water.toFixed(0);
            const sum = sums.get(component.type) ?? {air:0,water:0};
            sum.air += air; sum.water += water; sums.set(component.type,sum);
            airTotal += air; waterTotal += water;
        }
        for (const [type,sum] of [...sums, ['all', {air:airTotal,water:waterTotal}] as const]) {
            const row = components.querySelector<HTMLElement>(`[data-weight-summary="${type}"]`)!;
            row.querySelector<HTMLElement>('[data-sum-air]')!.textContent = sum.air.toFixed(0);
            row.querySelector<HTMLElement>('[data-sum-water]')!.textContent = sum.water.toFixed(0);
        }
    }
    function renderComponents(): void {
        components.replaceChildren();
        for (const component of [...properties!.components].sort((a,b) => a.type.localeCompare(b.type))) {
            const row = document.createElement('tr');
            cell(row, `${component.name} (${component.componentId})`); cell(row, component.type);
            if (component.eaN === null) cell(row, component.type === 'beam' ? 'Rigid' : '—');
            else input(row, component.componentId, 'eaN', component.eaN);
            input(row, component.componentId, 'lengthM', component.lengthM, component.type === 'beam' || component.lengthM === 0);
            const mass = input(row, component.componentId, 'massKg', component.massKg);
            const air = input(row, component.componentId, 'weightInAirN', component.lengthM > 0 ? component.weightInAirN / component.lengthM : 0);
            input(row, component.componentId, 'weightInWaterN', component.lengthM > 0 ? component.weightInWaterN / component.lengthM : 0);
            cell(row, '').dataset.totalAir = String(component.componentId);
            cell(row, '').dataset.totalWater = String(component.componentId);
            mass.addEventListener('input', () => { setField(air, 'weightInAirN', value(component.componentId, 'massKg') * GRAVITY / (value(component.componentId, 'lengthM') || 1)); });
            air.addEventListener('input', () => { setField(mass, 'massKg', value(component.componentId, 'weightInAirN') / GRAVITY); });
            row.querySelector<HTMLInputElement>('[data-property="lengthM"]')!.addEventListener('input', () => { setField(mass, 'massKg', value(component.componentId, 'weightInAirN') / GRAVITY); });
            components.append(row);
        }
        for (const type of [...new Set(properties!.components.map(component => component.type)), 'all']) {
            const row = document.createElement('tr'); row.dataset.weightSummary = type;
            row.className = 'winch-weight-summary';
            const label = cell(row, type === 'all' ? 'Grand total' : `${type} subtotal`);
            (label as HTMLTableCellElement).colSpan = 7;
            cell(row, '').dataset.sumAir = ''; cell(row, '').dataset.sumWater = '';
            if (type === 'all') components.append(row);
            else {
                const matches = [...components.querySelectorAll('tr')].filter(item => item.children[1]?.textContent === type);
                matches.at(-1)!.after(row);
            }
        }
        updateWeightTotals();
    }
    function updateSchedule(): void {
        updateWeightTotals();
        const seconds = Number(duration.value);
        for (const winch of properties?.winches ?? []) {
            const rate = winches.querySelector<HTMLElement>(`[data-rate="${winch.componentId}"]`);
            if (rate) rate.textContent = seconds > 0 ? display(value(winch.componentId, 'payoutM') / seconds) : '—';
            const component = properties!.components.find(row => row.componentId === winch.componentId)!;
            const paths = winches.querySelector<HTMLElement>(`[data-paths="${winch.componentId}"]`);
            if (paths && component.lengthM > 0) paths.textContent = winch.pathLengthsM.map(length => (length * value(winch.componentId, 'lengthM') / component.lengthM).toFixed(2)).join(', ');
        }
        const delta = Number(interval.value);
        element('winch-save-summary').textContent = seconds > 0 && delta > 0
            ? `${Math.ceil(seconds / delta) + 1} saved states, including the initial state and the final state.` : '';
    }
    function render(): void {
        renderComponents(); winches.replaceChildren();
        for (const winch of properties!.winches) {
            const row = document.createElement('tr');
            cell(row, `${winch.name} (${winch.componentId})`);
            cell(row, String(winch.pathLengthsM.length));
            cell(row, winch.pathLengthsM.map(length => length.toFixed(2)).join(', ')).dataset.paths = String(winch.componentId);
            const payout = input(row, winch.componentId, 'payoutM', 0);
            payout.min = '-10000'; payout.max = '10000';
            cell(row, '').dataset.rate = String(winch.componentId);
            winches.append(row);
        }
        updateSchedule(); submit.disabled = false; reset.disabled = false;
    }
    async function checkResponse(response: Response): Promise<void> {
        if (response.ok) return;
        const body = await response.text();
        let detail = body;
        try {
            const data = JSON.parse(body) as { error?: string; runId?: string; diagnosticsPath?: string };
            detail = data.error || body;
            saveDiagnostic({ serverRunId: data.runId, serverDiagnosticsPath: data.diagnosticsPath, serverResponse: data });
            if (data.diagnosticsPath) detail += `\nDiagnostics saved: ${data.diagnosticsPath}`;
        } catch { /* Plain server response. */ }
        throw new Error(detail || `Local server failed (HTTP ${response.status}).`);
    }
    propertiesButton.addEventListener('click', () => {
        if (!file || running) return;
        propertiesDialog.showModal();
    });
    propertiesForm.addEventListener('submit', event => {
        event.preventDefault();
        if (!propertiesForm.reportValidity()) return;
        updateSchedule(); propertiesDialog.close();
    });
    propertiesDialog.addEventListener('cancel', event => {
        if (!propertiesForm.reportValidity()) event.preventDefault();
    });
    propertiesForm.addEventListener('input', updateSchedule);
    runButton.addEventListener('click', () => {
        if (!file || !properties || running) return;
        if (!propertiesForm.reportValidity()) {
            propertiesDialog.showModal();
            propertiesForm.reportValidity();
            return;
        }
        element('winch-model-name').textContent = `Model: ${file.name}`;
        updateSchedule(); dialog.showModal();
    });
    cancel.addEventListener('click', () => dialog.close());
    dialog.addEventListener('cancel', event => { if (running) event.preventDefault(); });
    reset.addEventListener('click', () => { if (properties && !running) renderComponents(); updateSchedule(); });
    form.addEventListener('input', updateSchedule);
    form.addEventListener('submit', event => {
        event.preventDefault();
        if (running || !file || !properties || !form.reportValidity() || !propertiesForm.reportValidity()) return;
        const controls: SimulationControls = {
            components: properties.components.flatMap(component => {
                const changes: SimulationControls['components'][number] = { componentId: component.componentId };
                const editable = ['massKg', 'weightInWaterN', ...(component.eaN !== null ? ['eaN'] : []),
                    ...(component.type !== 'beam' && component.lengthM > 0 ? ['lengthM'] : [])] as const;
                for (const property of editable) {
                    const next = value(component.componentId, property);
                    const original = component[property as keyof typeof component] as number;
                    if (Math.abs(next - original) > 1e-10 * Math.max(1, Math.abs(original))) {
                        Object.assign(changes, { [property]: next });
                    }
                }
                return Object.keys(changes).length > 1 ? [changes] : [];
            }),
            winches: properties.winches.map(winch => ({ componentId: winch.componentId, payoutM: value(winch.componentId, 'payoutM') })),
            durationSeconds: Number(duration.value), saveIntervalSeconds: Number(interval.value),
            solverStrategy: element<HTMLSelectElement>('winch-solver').value as SimulationControls['solverStrategy'],
            initialIncrementM: Number(element<HTMLInputElement>('winch-increment').value) / 1000
        };
        diagnostic = null;
        saveDiagnostic({ startedAt: new Date().toISOString(), status: 'running', stage: 'request',
            modelName: file.name, modelBytes: file.size, controls, componentProperties: properties.components,
            pageUrl: window.location.href, endpoint: new URL('/api/simulate',window.location.href).href,
            userAgent: navigator.userAgent });
        const current = revision; const sourceXml = modelXml;
        running = true; propertiesButton.disabled = true; runButton.disabled = true; submit.disabled = true; cancel.disabled = true; reset.disabled = true;
        const fields = [...form.querySelectorAll<HTMLInputElement | HTMLSelectElement>('input, select')]; fields.forEach(field => { field.disabled = true; });
        status.textContent = controls.solverStrategy === 'time-marching'
            ? 'Solving implicit time marching locally. Keep this window open.'
            : 'Solving parallel quasi-static equilibrium states locally. Keep this window open.';
        void (async () => {
            try {
                const requestXml = sourceXml;
                if (current !== revision) throw new Error('The loaded model changed. Run again using the current model.');
                const response = await fetch('/api/simulate', {
                    method: 'POST', headers: { 'X-WinchAI-Request': '1', 'Content-Type': 'application/json' },
                    body: JSON.stringify({ modelXml: requestXml, controls })
                });
                saveDiagnostic({ httpStatus: response.status, stage: 'response',
                    serverRunId: response.headers?.get('X-WinchAI-Run-ID'),
                    serverDiagnosticsPath: response.headers?.get('X-WinchAI-Diagnostics-Path') });
                await checkResponse(response);
                const buffer = await response.arrayBuffer();
                if (current !== revision) throw new Error('The loaded model changed. Run again using the current model.');
                saveDiagnostic({ stage: 'playback', resultBytes: buffer.byteLength });
                playback.loadResult(buffer); saveDiagnostic({ status: 'succeeded', stage: 'complete' }); dialog.close();
                status.textContent = 'All saved equilibrium states converged. Results are loaded in the playback panel.';
            } catch (error) {
                const message = error instanceof Error ? error.message : String(error);
                let detail = message;
                if (error instanceof TypeError && /fetch|network|load failed/i.test(message)) {
                    detail = `No response received from the local simulation API (${new URL('/api/simulate',window.location.href).href}). The Python server may be stopped, the connection interrupted, or the page running without an API. Start npm run winch and open http://127.0.0.1:8765/. If using npm run dev, also run python -m simulation.local_server --api-only. Original error: ${message}`;
                }
                saveDiagnostic({ status: 'failed', error: message, explanation: detail,
                    stack: error instanceof Error ? error.stack : undefined });
                status.textContent = `Simulation failed: ${detail}\nRun settings and diagnostics are saved in this browser. Click Download run diagnostics.`;
            } finally {
                running = false; propertiesButton.disabled = !file; runButton.disabled = !properties; fields.forEach(field => { field.disabled = false; });
                submit.disabled = !properties; reset.disabled = !properties; cancel.disabled = false;
            }
        })();
    });
    return { setModelFile(next): void {
        revision++; file = next; properties = null; modelXml = null;
        components.replaceChildren(); winches.replaceChildren(); submit.disabled = true; reset.disabled = true;
        runButton.disabled = true; propertiesButton.disabled = next === null;
        element('component-model-name').textContent = next ? `Model: ${next.name}` : '';
        propertiesStatus.textContent = next ? 'Reading component properties from the model file…' : '';
        runButton.title = next ? 'Loading simulation properties…' : 'Load a model before running the winch simulation';
        if (!next) return;
        const current = revision;
        void (async () => {
            try {
                const xml = await readModelFile(next);
                const result = readModelProperties(xml);
                if (current !== revision) return;
                modelXml = xml; properties = result; render();
                propertiesStatus.textContent = 'Properties loaded. Changes are used by subsequent simulations.';
                runButton.disabled = running;
                runButton.title = `Configure the local winch simulation using ${next.name}`;
            } catch (error) {
                if (current !== revision) return;
                propertiesStatus.textContent = `Unable to read model properties: ${error instanceof Error ? error.message : String(error)}.`;
                runButton.title = 'Open Component Properties to see the model loading error.';
            }
        })();
    } };
}
