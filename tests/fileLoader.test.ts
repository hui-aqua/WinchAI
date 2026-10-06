import { describe, it, expect, vi } from 'vitest';
import { setupFileLoader } from '@/ui/fileLoader';

function setup() {
    document.body.innerHTML = '<input type="file"><main><div id="drop-overlay"></div></main>';
    const input = document.querySelector('input')!;
    const zone = document.querySelector('main')!;
    const overlay = document.getElementById('drop-overlay')!;
    const onLoadSuccess = vi.fn();
    const onLoadError = vi.fn();
    setupFileLoader(input, zone, { onLoadSuccess, onLoadError });
    return { zone, overlay, onLoadSuccess, onLoadError };
}

describe('file drag and drop', () => {
    it('clears the overlay and loads the dropped model', async () => {
        const { zone, overlay, onLoadSuccess, onLoadError } = setup();
        zone.dispatchEvent(new Event('dragover', { bubbles: true, cancelable: true }));
        expect(overlay.classList.contains('drag-active')).toBe(true);
        const file = new File(['<model><Nodes><node id="1" x="0" y="0" z="0"/></Nodes></model>'], 'dropped.amodel');
        const drop = new Event('drop', { bubbles: true, cancelable: true });
        Object.defineProperty(drop, 'dataTransfer', { value: { files: [file] } });
        zone.dispatchEvent(drop);
        expect(drop.defaultPrevented).toBe(true);
        expect(overlay.classList.contains('drag-active')).toBe(false);
        await vi.waitFor(() => expect(onLoadSuccess).toHaveBeenCalled());
        expect(onLoadSuccess.mock.calls[0][1]).toBe('dropped.amodel');
        expect(onLoadError).not.toHaveBeenCalled();
    });

    it('keeps the overlay visible within the viewport and clears it on exit', () => {
        const { zone, overlay } = setup();
        zone.dispatchEvent(new Event('dragover', { bubbles: true, cancelable: true }));
        const internalLeave = new Event('dragleave', { bubbles: true, cancelable: true });
        Object.defineProperty(internalLeave, 'relatedTarget', { value: overlay });
        zone.dispatchEvent(internalLeave);
        expect(overlay.classList.contains('drag-active')).toBe(true);
        zone.dispatchEvent(new Event('dragleave', { bubbles: true, cancelable: true }));
        expect(overlay.classList.contains('drag-active')).toBe(false);
    });
});
