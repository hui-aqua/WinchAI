import * as THREE from 'three';
import type { AquaSimNode, MembraneElement, StructuralComponent } from '@/parser/types';
import { aquaSimToThree } from '../cameraUtils';

export interface MembraneRenderOptions {
    defaultColor?: number;
    opacity?: number;
    wireframe?: boolean;
}

/**
 * Renders Membrane structural components (nets, flaps, panels).
 * Three-node elements render as [A, B, C].
 * Each quadrilateral element (A, B, C, D) is split into two triangles:
 *   Triangle 1: [A, B, C]
 *   Triangle 2: [A, C, D]
 * Rendered using batched THREE.BufferGeometry with DoubleSide transparent material.
 */
export function createMembraneGroup(
    membranes: StructuralComponent<MembraneElement>[],
    nodes: Map<number, AquaSimNode>,
    options: MembraneRenderOptions = {}
): THREE.Group {
    const membraneGroup = new THREE.Group();
    membraneGroup.name = 'membraneGroup';

    // Translucent emerald/teal for nets and membranes
    const defaultColor = options.defaultColor ?? 0x10b981;
    const opacity = options.opacity ?? 0.65;
    const wireframe = options.wireframe ?? false;

    membranes.forEach((comp, compIndex) => {
        if (!comp.elements || comp.elements.length === 0) return;

        const positions: number[] = [];
        const nodeIds: number[] = [];

        for (const el of comp.elements) {
            const nA = nodes.get(el.nodeA);
            const nB = nodes.get(el.nodeB);
            const nC = nodes.get(el.nodeC);
            const nD = el.nodeD === undefined ? undefined : nodes.get(el.nodeD);

            if (nA && nB && nC && (el.nodeD === undefined || nD)) {
                const pA = aquaSimToThree(nA.x, nA.y, nA.z);
                const pB = aquaSimToThree(nB.x, nB.y, nB.z);
                const pC = aquaSimToThree(nC.x, nC.y, nC.z);

                // Triangle 1: A -> B -> C
                positions.push(pA.x, pA.y, pA.z);
                positions.push(pB.x, pB.y, pB.z);
                positions.push(pC.x, pC.y, pC.z);
                nodeIds.push(el.nodeA, el.nodeB, el.nodeC);

                // Triangle 2: A -> C -> D
                if (nD) {
                    const pD = aquaSimToThree(nD.x, nD.y, nD.z);
                    positions.push(pA.x, pA.y, pA.z);
                    positions.push(pC.x, pC.y, pC.z);
                    positions.push(pD.x, pD.y, pD.z);
                    nodeIds.push(el.nodeA, el.nodeC, el.nodeD!);
                }
            }
        }

        if (positions.length === 0) return;

        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute(
            'position',
            new THREE.Float32BufferAttribute(positions, 3)
        );
        geometry.computeVertexNormals();

        const compColor = comp.color
            ? new THREE.Color(comp.color.r, comp.color.g, comp.color.b)
            : defaultColor;

        // Solid translucent net surface with depthWrite: false to prevent occlusion of nested nets
        const material = new THREE.MeshStandardMaterial({
            color: compColor,
            side: THREE.DoubleSide,
            transparent: true,
            opacity: opacity,
            depthWrite: false,
            wireframe: wireframe,
            roughness: 0.6,
            metalness: 0.1
        });

        const mesh = new THREE.Mesh(geometry, material);
        mesh.name = `membrane_solid_${comp.id ?? compIndex}`;

        // Wireframe netting overlay to highlight net twine grid
        const wireMaterial = new THREE.MeshBasicMaterial({
            color: compColor,
            wireframe: true,
            transparent: true,
            opacity: 0.4,
            depthWrite: false
        });
        const wireMesh = new THREE.Mesh(geometry, wireMaterial);
        wireMesh.name = `membrane_wire_${comp.id ?? compIndex}`;

        const compGroup = new THREE.Group();
        compGroup.name = `membrane_${comp.id ?? compIndex}_${comp.name}`;
        compGroup.add(mesh);
        compGroup.add(wireMesh);

        compGroup.userData = {
            aquaSimType: 'membrane',
            componentName: comp.name,
            componentId: comp.id ?? compIndex,
            elementCount: comp.elements.length,
            nodeIds,
            metadata: comp.metadata
        };

        membraneGroup.add(compGroup);
    });

    return membraneGroup;
}

/**
 * Updates vertex positions and normals for all membrane meshes based on current dynamic node positions.
 */
export function updateMembraneState(
    membraneGroup: THREE.Group,
    nodePositions: ReadonlyMap<number, THREE.Vector3>
): void {
    for (const compGroup of membraneGroup.children) {
        const nodeIds = compGroup.userData?.nodeIds as number[] | undefined;
        if (!nodeIds || nodeIds.length === 0) continue;

        // The meshes in compGroup share the same geometry
        const mesh = compGroup.children.find((c) => c instanceof THREE.Mesh) as THREE.Mesh | undefined;
        if (!mesh) continue;

        const geometry = mesh.geometry as THREE.BufferGeometry;
        const posAttr = geometry.getAttribute('position') as THREE.BufferAttribute;
        if (!posAttr) continue;

        let modified = false;
        for (let i = 0; i < nodeIds.length; i++) {
            const nodeId = nodeIds[i];
            const p = nodePositions.get(nodeId);
            if (p) {
                posAttr.setXYZ(i, p.x, p.y, p.z);
                modified = true;
            }
        }

        if (modified) {
            posAttr.needsUpdate = true;
            geometry.computeVertexNormals();
            geometry.computeBoundingSphere();
        }
    }
}
