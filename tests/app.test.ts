import { readFileSync } from 'node:fs';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { initializeApp } from '@/app/initializeApp';

const viewer = vi.hoisted(() => ({
    setTheme: vi.fn(),
    getCameraMode: vi.fn(() => 'perspective'),
    toggleCameraMode: vi.fn(() => 'orthographic'),
    fitView: vi.fn(),
    fitCageView: vi.fn(),
    alignView: vi.fn(),
    setRenderMode: vi.fn(),
    setGridVisibility: vi.fn(),
    setAxesVisibility: vi.fn()
}));

vi.mock('@/viewer/AquaSimViewer', () => ({
    AquaSimViewer: vi.fn(function () { return viewer; })
}));

describe('application UI wiring', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        localStorage.clear();
        const html = readFileSync('index.html', 'utf8');
        document.body.innerHTML = html.match(/<body>([\s\S]*)<\/body>/)![1]
            .replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
    });

    it('connects the production HTML controls to the viewer', () => {
        initializeApp();
        const click = (id: string) => document.getElementById(id)!.click();
        click('btn-fit-view');
        click('btn-fit-cage');
        expect(viewer.fitView).toHaveBeenCalledOnce();
        expect(viewer.fitCageView).toHaveBeenCalledOnce();
        for (const [id, axis] of [['x', 'x+'], ['y', 'y+'], ['z', 'z+'], ['iso', 'iso']]) {
            click(`btn-view-${id}`);
            expect(viewer.alignView).toHaveBeenLastCalledWith(axis);
        }
        click('btn-toggle-camera');
        expect(viewer.toggleCameraMode).toHaveBeenCalledOnce();
        expect(document.getElementById('btn-toggle-camera')!.getAttribute('aria-checked')).toBe('true');
        click('btn-toggle-grid');
        click('btn-toggle-axes');
        expect(viewer.setGridVisibility).toHaveBeenLastCalledWith(true);
        expect(viewer.setAxesVisibility).toHaveBeenLastCalledWith(true);
        const select = document.getElementById('select-render-mode') as HTMLSelectElement;
        select.value = 'wireframe';
        select.dispatchEvent(new Event('change'));
        expect(viewer.setRenderMode).toHaveBeenLastCalledWith('wireframe');
        const input = document.getElementById('file-input')!;
        const open = vi.spyOn(input, 'click');
        click('btn-open-file');
        expect(open).toHaveBeenCalledOnce();
    });

    it('restores and updates theme preferences and sidebar visibility', () => {
        localStorage.setItem('amodel-theme', 'light');
        initializeApp();
        expect(viewer.setTheme).toHaveBeenLastCalledWith('light');
        document.getElementById('btn-toggle-theme')!.click();
        expect(viewer.setTheme).toHaveBeenLastCalledWith('dark');
        expect(document.documentElement.dataset.theme).toBe('dark');
        expect(localStorage.getItem('amodel-theme')).toBe('dark');
        const sidebar = document.getElementById('model-sidebar')!;
        const initiallyHidden = sidebar.hidden;
        const button = document.getElementById('btn-toggle-sidebar')!;
        button.click();
        expect(sidebar.hidden).toBe(!initiallyHidden);
        expect(button.getAttribute('aria-expanded')).toBe(String(initiallyHidden));
    });
});
