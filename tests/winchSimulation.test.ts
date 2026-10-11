import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setupWinchSimulation } from '@/ui/winchSimulation';

describe('local winch simulation controls', () => {
    const playback = {
        setModel: vi.fn(),
        loadResult: vi.fn()
    };

    beforeEach(() => {
        vi.clearAllMocks();
        const html = readFileSync('index.html', 'utf8');
        document.body.innerHTML = html.match(/<body>([\s\S]*)<\/body>/)![1]
            .replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
    });

    afterEach(() => vi.unstubAllGlobals());

    it('enables after loading a model and automatically loads the returned result', async () => {
        const simulation = setupWinchSimulation(playback);
        const runButton = document.getElementById('btn-run-winch-simulation') as HTMLButtonElement;
        expect(runButton.disabled).toBe(true);

        const modelFile = new File(['<model />'], 'cage.amodel');
        simulation.setModelFile(modelFile);
        expect(runButton.disabled).toBe(false);
        runButton.click();

        const result = new Uint8Array([65, 81, 87, 82]).buffer;
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true,
            arrayBuffer: async () => result
        });
        vi.stubGlobal('fetch', fetchMock);
        const form = document.getElementById('winch-simulation-form')!;
        form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
        form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));

        await vi.waitFor(() => expect(playback.loadResult).toHaveBeenCalledWith(result));
        expect(fetchMock).toHaveBeenCalledOnce();
        const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
        expect(url).toContain('/api/simulate?');
        expect(url).toContain('payout_m=1');
        expect(url).toContain('initial_increment_m=0.05');
        expect(options.method).toBe('POST');
        expect(options.body).toBe(modelFile);
        expect(new Headers(options.headers).get('X-WinchAI-Request')).toBe('1');
    });

    it('offers the 50 mm continuation step and no dynamic solver selector', () => {
        expect((document.getElementById('winch-increment') as HTMLInputElement).value).toBe('50');
        expect(document.getElementById('winch-solver')).toBeNull();
        expect(document.getElementById('winch-water-depth')).toBeNull();
    });
    it('discards a result if the model is replaced during a pending run', async () => {
        const simulation = setupWinchSimulation(playback);
        simulation.setModelFile(new File(['first'], 'first.amodel'));
        let resolveResponse!: (response: { ok: boolean; arrayBuffer: () => Promise<ArrayBuffer> }) => void;
        vi.stubGlobal('fetch', vi.fn(() => new Promise(resolve => { resolveResponse = resolve; })));
        document.getElementById('winch-simulation-form')!
            .dispatchEvent(new Event('submit', { cancelable: true }));
        simulation.setModelFile(new File(['second'], 'second.amodel'));
        resolveResponse({ ok: true, arrayBuffer: async () => new ArrayBuffer(0) });
        await vi.waitFor(() => expect((document.getElementById('btn-submit-winch-simulation') as HTMLButtonElement).disabled).toBe(false));
        expect(playback.loadResult).not.toHaveBeenCalled();
        expect(document.getElementById('winch-run-status')!.textContent).toContain('model changed');
    });

});
