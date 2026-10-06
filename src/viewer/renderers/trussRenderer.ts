import type { Group } from 'three';
import type { AquaSimNode, LineElement, StructuralComponent } from '@/parser/types';
import { createLineGroup, updateLineScale } from './lineRenderer';
import type { LineRenderOptions } from './lineRenderer';

export type TrussRenderOptions = LineRenderOptions;

export function createTrussGroup(
    components: StructuralComponent<LineElement>[],
    nodes: Map<number, AquaSimNode>,
    options: TrussRenderOptions = {}
): Group {
    return createLineGroup(components, nodes, {
        type: 'truss',
        defaultColor: 0xffaa00,
        defaultRadius: 0.015,
        radialSegments: 8,
        roughness: 0.55,
        metalness: 0.1
    }, options);
}

export const updateTrussScale = updateLineScale;
