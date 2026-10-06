import { describe, it, expect, vi } from 'vitest';
import { ModelTreeUI } from '@/ui/modelTree';
import type { AquaSimViewer } from '@/viewer/AquaSimViewer';
import { parseAmodelXml } from '@/parser/amodelParser';

describe('single-model hierarchy', () => {
    it('replaces the previous hierarchy and statistics on import', () => {
        const viewer = { setComponentVisibility: vi.fn() };
        const treeContainer = document.createElement('div');
        const statsContainer = document.createElement('div');
        const ui = new ModelTreeUI({ treeContainer, statsContainer }, viewer as unknown as AquaSimViewer);
        const makeModel = (name: string) => parseAmodelXml(`<model><Nodes><node id="1" x="0" y="0" z="0"/><node id="2" x="10" y="0" z="0"/></Nodes><Components><beam id="1" name="${name}"><elements><element id="1" StartNode_ID="1" EndNode_ID="2"/></elements></beam></Components></model>`);
        ui.update(makeModel('First beam'), 'first.amodel');
        ui.update(makeModel('Second beam'), 'second.amodel');
        expect(treeContainer.textContent).not.toContain('First beam');
        expect(treeContainer.textContent).toContain('Second beam');
        expect(statsContainer.textContent).toContain('second.amodel');
        expect(statsContainer.textContent).not.toContain('first.amodel');
        const component = treeContainer.querySelector('[data-key="beam_1"]') as HTMLInputElement;
        component.checked = false; component.dispatchEvent(new Event('change'));
        expect(viewer.setComponentVisibility).toHaveBeenCalledWith('beam_1', false);
    });
});
