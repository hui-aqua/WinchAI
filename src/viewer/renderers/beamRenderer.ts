import type { Group } from 'three';
import type { AquaSimNode, LineElement, StructuralComponent } from '@/parser/types';
import { createLineGroup, updateLineScale } from './lineRenderer';
import type { LineRenderOptions } from './lineRenderer';

export type BeamRenderOptions = LineRenderOptions;

export function createBeamGroup(
    components: StructuralComponent<LineElement>[],
    nodes: Map<number, AquaSimNode>,
    options: BeamRenderOptions = {}
): Group {
    return createLineGroup(components, nodes, {
        type: 'beam',
        defaultColor: 0x00d2ff,
        defaultRadius: 0.1,
        radialSegments: 16,
        roughness: 0.45,
        metalness: 0.15
    }, options);
}

export const updateBeamScale = updateLineScale;
