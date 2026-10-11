import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setupWinchSimulation } from '@/ui/winchSimulation';

const modelXml = `<model><Nodes>
<node id="1" x="-1" y="0" z="0"/><node id="2" x="1" y="0" z="0"/>
<node id="3" x="-1" y="0" z="-2"/><node id="4" x="1" y="0" z="-2"/>
</Nodes><Components><beam id="1" name="Beam"><materials weightInAir="10" weightInWater="5"/>
<element StartNode_ID="3" EndNode_ID="4"/></beam>
${[2,3].map(id => `<truss id="${id}" name="winch${id-1}"><mooring areal="0.001" young="1e8" weightInAir="0" weightInWater="0"/><element StartNode_ID="${id-1}" EndNode_ID="${id+1}"/></truss>`).join('')}
</Components></model>`;
const field = (id: number, property: string) => document.querySelector<HTMLInputElement>(`input[data-component-id="${id}"][data-property="${property}"]`)!;
const submit = () => document.getElementById('winch-simulation-form')!.dispatchEvent(new Event('submit', { cancelable: true }));

describe('local winch simulation controls', () => {
    const playback = { setModel: vi.fn(), loadResult: vi.fn() };
    beforeEach(() => {
        vi.clearAllMocks();
        document.body.innerHTML = readFileSync('index.html', 'utf8').match(/<body>([\s\S]*)<\/body>/)![1].replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
    });
    afterEach(() => vi.unstubAllGlobals());
    async function open(fetchMock: ReturnType<typeof vi.fn>) {
        vi.stubGlobal('fetch', fetchMock);
        const simulation = setupWinchSimulation(playback);
        const file = new File([modelXml], 'cage.amodel');
        Object.defineProperty(file, 'text', { value: async () => modelXml });
        simulation.setModelFile(file);
        await vi.waitFor(() => expect(field(2, 'eaN')).not.toBeNull());
        document.getElementById('btn-component-properties')!.click();
        return simulation;
    }
    it('inspects properties, links mass and air weight, and keeps rigid geometry read-only', async () => {
        const fetchMock = vi.fn().mockRejectedValue(new Error('Server unavailable'));
        await open(fetchMock);
        expect(fetchMock).not.toHaveBeenCalled();
        expect(field(1, 'lengthM').readOnly).toBe(true);
        expect(field(1, 'eaN')).toBeNull();
        field(1, 'massKg').value = '40'; field(1, 'massKg').dispatchEvent(new Event('input', { bubbles: true }));
        expect(Number(field(1, 'weightInAirN').value)).toBe(196);
        field(1, 'weightInAirN').value = '98.0665'; field(1, 'weightInAirN').dispatchEvent(new Event('input', { bubbles: true }));
        expect(Number(field(1, 'massKg').value)).toBeCloseTo(20);
        document.getElementById('btn-reset-winch-properties')!.click();
        expect(field(1, 'massKg').value).toBe('20');
    });
    it('sends edited properties, independent signed payouts and time settings once and loads results', async () => {
        const result = new ArrayBuffer(4);
        const fetchMock = vi.fn().mockResolvedValue({ ok: true, arrayBuffer: async () => result });
        await open(fetchMock);
        field(2, 'eaN').value = '200000'; field(2, 'payoutM').value = '1'; field(3, 'payoutM').value = '-0.5';
        (document.getElementById('winch-duration') as HTMLInputElement).value = '10';
        (document.getElementById('winch-save-interval') as HTMLInputElement).value = '2';
        field(2, 'payoutM').dispatchEvent(new Event('input', { bubbles: true }));
        expect(document.querySelector('[data-rate="2"]')!.textContent).toBe('0.1');
        document.getElementById('component-properties-form')!.dispatchEvent(new Event('submit', { cancelable: true }));
        document.getElementById('btn-run-winch-simulation')!.click();
        expect(document.getElementById('winch-simulation-form')!.contains(field(2, 'eaN'))).toBe(false);
        expect(field(2, 'eaN').value).toBe('200000');
        submit(); submit();
        await vi.waitFor(() => expect(playback.loadResult).toHaveBeenCalledWith(result));
        expect(fetchMock).toHaveBeenCalledTimes(1);
        const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
        expect(url).toBe('/api/simulate');
        const body = JSON.parse(options.body as string);
        expect(body.controls.winches).toEqual([{componentId:2,payoutM:1},{componentId:3,payoutM:-0.5}]);
        expect(body.controls.components[0].eaN).toBe(200000);
        expect(body.controls.components).toEqual([{componentId:2,eaN:200000}]);
        expect(body.controls.durationSeconds).toBe(10);
        expect(body.controls.saveIntervalSeconds).toBe(2);
        expect(body.controls.initialIncrementM).toBe(0.05);
        expect(body.modelXml).toBe(modelXml);
    });
    it('loads properties on import and preserves edits when reopening the properties dialog', async () => {
        const simulation = setupWinchSimulation(playback);
        const file = new File([modelXml], 'cage.amodel');
        Object.defineProperty(file, 'text', { value: async () => modelXml });
        simulation.setModelFile(file);
        await vi.waitFor(() => expect(field(2, 'eaN')).not.toBeNull());
        expect((document.getElementById('component-properties-dialog') as HTMLDialogElement).open).toBe(false);
        document.getElementById('btn-component-properties')!.click();
        field(2, 'eaN').value = '300000';
        document.getElementById('component-properties-form')!.dispatchEvent(new Event('submit', { cancelable: true }));
        document.getElementById('btn-component-properties')!.click();
        expect(field(2, 'eaN').value).toBe('300000');
        const replacement = new File([modelXml], 'replacement.amodel');
        Object.defineProperty(replacement, 'text', { value: async () => modelXml });
        simulation.setModelFile(replacement);
        await vi.waitFor(() => expect(field(2, 'eaN')?.value).toBe('100000'));
    });
    it('shows per-metre weights, totals and live subtotals with requested precision', async () => {
        await open(vi.fn());
        expect(field(1, 'lengthM').value).toBe('2.00');
        expect(field(1, 'massKg').value).toBe('20');
        expect(field(1, 'weightInAirN').value).toBe('98');
        expect(field(1, 'weightInWaterN').value).toBe('49');
        expect(document.querySelector('[data-total-air="1"]')!.textContent).toBe('196');
        expect(document.querySelector('[data-total-water="1"]')!.textContent).toBe('98');
        expect(document.querySelector('[data-weight-summary="beam"] [data-sum-air]')!.textContent).toBe('196');
        field(1, 'massKg').value = '40';
        field(1, 'massKg').dispatchEvent(new Event('input', { bubbles:true }));
        expect(document.querySelector('[data-total-air="1"]')!.textContent).toBe('392');
        expect(document.querySelector('[data-weight-summary="all"] [data-sum-air]')!.textContent).toBe('392');
    });
    it('preserves settings and actionable diagnostics when the API cannot be reached', async () => {
        await open(vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
        submit();
        await vi.waitFor(() => expect(document.getElementById('winch-run-status')!.textContent).toContain('No response received'));
        const report = JSON.parse(localStorage.getItem('winchai-last-run')!);
        expect(report.status).toBe('failed');
        expect(report.error).toBe('Failed to fetch');
        expect(report.controls.durationSeconds).toBe(60);
        expect(report.modelName).toBe('cage.amodel');
        expect(report.endpoint).toContain('/api/simulate');
        expect((document.getElementById('btn-download-winch-diagnostics') as HTMLButtonElement).disabled).toBe(false);
    });
    it('discards a result when the loaded model changes', async () => {
        let resolve!: (response: unknown) => void;
        const fetchMock = vi.fn().mockImplementation(() => new Promise(done => { resolve = done; }));
        const simulation = await open(fetchMock);
        submit(); await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
        simulation.setModelFile(new File(['second'], 'second.amodel'));
        resolve({ ok: true, arrayBuffer: async () => new ArrayBuffer(0) });
        await vi.waitFor(() => expect(document.getElementById('winch-run-status')!.textContent).toContain('model changed'));
        expect(playback.loadResult).not.toHaveBeenCalled();
        expect((document.getElementById('btn-submit-winch-simulation') as HTMLButtonElement).disabled).toBe(true);
    });
});
