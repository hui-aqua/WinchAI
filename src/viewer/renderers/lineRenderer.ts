import * as THREE from 'three';
import type { AquaSimNode, LineElement, StructuralComponent } from '@/parser/types';
import { aquaSimToThree } from '../cameraUtils';

export interface LineRenderOptions {
    defaultColor?: number;
    scaleFactor?: number;
    renderMode?: 'solid' | 'wireframe' | 'both';
}

interface LineElementTransform {
    mid: THREE.Vector3;
    quat: THREE.Quaternion;
    length: number;
    baseScaleX: number;
    baseScaleZ: number;
    startNodeId: number;
    endNodeId: number;
    elementId: number;
}

interface LineStyle {
    type: 'beam' | 'truss';
    defaultColor: number;
    defaultRadius: number;
    radialSegments: number;
    roughness: number;
    metalness: number;
}

/** Build solid sections and centerlines for beam or truss components. */
export function createLineGroup(
    components: StructuralComponent<LineElement>[],
    nodes: Map<number, AquaSimNode>,
    style: LineStyle,
    options: LineRenderOptions = {}
): THREE.Group {
    const lineGroup = new THREE.Group();
    lineGroup.name = `${style.type}Group`;

    const defaultColor = options.defaultColor ?? style.defaultColor;
    const scaleFactor = options.scaleFactor ?? 1.0;
    const renderMode = options.renderMode ?? 'both';
    let boxGeometry: THREE.BoxGeometry | undefined;
    let cylinderGeometry: THREE.CylinderGeometry | undefined;

    components.forEach((comp, compIndex) => {
        if (!comp.elements || comp.elements.length === 0) return;

        const compColor = comp.color
            ? new THREE.Color(comp.color.r, comp.color.g, comp.color.b)
            : new THREE.Color(defaultColor);

        const compGroup = new THREE.Group();
        compGroup.name = `${style.type}_comp_${comp.id ?? compIndex}`;

        // Determine cross-section from amodel
        const section = comp.section;
        const isRectangular = style.type === 'beam' && (section?.shape === 'ibeam' || section?.shape === 'rectangular');
        const baseRadius = section?.radius ?? style.defaultRadius;
        const baseWidth = section?.width ?? (baseRadius * 2);
        const baseHeight = section?.height ?? (baseRadius * 2);

        // Precompute element transformations
        const transforms: LineElementTransform[] = [];
        const linePositions: number[] = [];
        const lineNodeIds: number[] = [];
        const upVector = new THREE.Vector3(0, 1, 0);

        for (const el of comp.elements) {
            const startNode = nodes.get(el.startNodeId);
            const endNode = nodes.get(el.endNodeId);

            if (startNode && endNode) {
                const p1 = aquaSimToThree(startNode.x, startNode.y, startNode.z);
                const p2 = aquaSimToThree(endNode.x, endNode.y, endNode.z);

                linePositions.push(p1.x, p1.y, p1.z, p2.x, p2.y, p2.z);
                lineNodeIds.push(el.startNodeId, el.endNodeId);

                const dir = new THREE.Vector3().subVectors(p2, p1);
                const length = dir.length();
                if (length < 1e-5) continue;

                dir.normalize();
                const quat = new THREE.Quaternion().setFromUnitVectors(upVector, dir);
                const mid = new THREE.Vector3().addVectors(p1, p2).multiplyScalar(0.5);

                const baseScaleX = isRectangular ? baseWidth : baseRadius;
                const baseScaleZ = isRectangular ? baseHeight : baseRadius;

                transforms.push({
                    mid,
                    quat,
                    length,
                    baseScaleX,
                    baseScaleZ,
                    startNodeId: el.startNodeId,
                    endNodeId: el.endNodeId,
                    elementId: el.id
                });
            }
        }

        if (transforms.length === 0) return;

        // 1. Solid 3D Instanced Mesh
        // Share primitives within this group, without sharing them between viewers.
        const geometry = isRectangular
            ? (boxGeometry ??= new THREE.BoxGeometry(1, 1, 1))
            : (cylinderGeometry ??= new THREE.CylinderGeometry(1, 1, 1, style.radialSegments));
        const solidMaterial = new THREE.MeshStandardMaterial({
            color: compColor,
            roughness: style.roughness,
            metalness: style.metalness
        });

        const instancedMesh = new THREE.InstancedMesh(geometry, solidMaterial, transforms.length);
        instancedMesh.name = `${style.type}_solid_${comp.id ?? compIndex}`;

        const matrix = new THREE.Matrix4();
        const scaleVec = new THREE.Vector3();

        transforms.forEach((t, i) => {
            scaleVec.set(
                t.baseScaleX * scaleFactor,
                t.length,
                t.baseScaleZ * scaleFactor
            );
            matrix.compose(t.mid, t.quat, scaleVec);
            instancedMesh.setMatrixAt(i, matrix);
        });
        instancedMesh.instanceMatrix.needsUpdate = true;
        instancedMesh.visible = renderMode !== 'wireframe';

        // 2. LineSegments for centerlines
        const lineGeom = new THREE.BufferGeometry();
        lineGeom.setAttribute('position', new THREE.Float32BufferAttribute(linePositions, 3));
        const lineMaterial = new THREE.LineBasicMaterial({
            color: compColor,
            linewidth: 1
        });
        const lineSegments = new THREE.LineSegments(lineGeom, lineMaterial);
        lineSegments.name = `${style.type}_line_${comp.id ?? compIndex}`;
        lineSegments.visible = renderMode !== 'solid';

        compGroup.add(instancedMesh);
        compGroup.add(lineSegments);

        compGroup.userData = {
            aquaSimType: style.type,
            componentName: comp.name,
            componentId: comp.id ?? compIndex,
            elementCount: comp.elements.length,
            section: comp.section,
            transforms,
            lineNodeIds,
            instancedMesh,
            lineSegments,
            metadata: comp.metadata
        };

        lineGroup.add(compGroup);
    });

    return lineGroup;
}

/**
 * Dynamically scale line cross-sections without recreating geometry
 */
export function updateLineScale(lineGroup: THREE.Group, scaleFactor: number): void {
    const matrix = new THREE.Matrix4();
    const scaleVec = new THREE.Vector3();

    lineGroup.children.forEach((compGroup) => {
        const u = compGroup.userData;
        const instancedMesh = u.instancedMesh as THREE.InstancedMesh | undefined;
        const transforms = u.transforms as LineElementTransform[] | undefined;

        if (instancedMesh && transforms) {
            transforms.forEach((t, i) => {
                scaleVec.set(
                    t.baseScaleX * scaleFactor,
                    t.length,
                    t.baseScaleZ * scaleFactor
                );
                matrix.compose(t.mid, t.quat, scaleVec);
                instancedMesh.setMatrixAt(i, matrix);
            });
            instancedMesh.instanceMatrix.needsUpdate = true;
        }
    });
}

/** Deform connected lines for a prescribed vertical displacement with pinned surface nodes. */
export function updateLineDisplacement(
    lineGroup: THREE.Group,
    nodes: Map<number, AquaSimNode>,
    displacementZM: number,
    fixedNodeIds: ReadonlySet<number>
): void {
    const positions = new Map<number, THREE.Vector3>();
    for (const [nodeId, node] of nodes) {
        positions.set(
            nodeId,
            new THREE.Vector3(node.x, node.y, node.z + (fixedNodeIds.has(nodeId) ? 0 : displacementZM))
        );
    }
    updateLineState(lineGroup, positions);
}

/** Apply a dynamic position field and colour rope elements by their axial tension. */
export function updateLineState(
    lineGroup: THREE.Group,
    nodePositions: ReadonlyMap<number, THREE.Vector3>,
    tensionByElementKey: ReadonlyMap<string, number> = new Map(),
    maximumTensionN = 0
): void {
    const matrix = new THREE.Matrix4();
    const scale = new THREE.Vector3();
    const up = new THREE.Vector3(0, 1, 0);
    for (const component of lineGroup.children) {
        const { transforms, lineNodeIds, instancedMesh, lineSegments } = component.userData as {
            transforms: LineElementTransform[];
            lineNodeIds: number[];
            instancedMesh: THREE.InstancedMesh;
            lineSegments: THREE.LineSegments;
        };
        const positions = lineSegments.geometry.getAttribute('position') as THREE.BufferAttribute;
        lineNodeIds.forEach((nodeId, index) => {
            const position = nodePositions.get(nodeId);
            if (!position) return;
            positions.setXYZ(index, position.x, position.y, position.z);
        });
        let maximumComponentTension = 0;
        transforms.forEach((transform, index) => {
            const start = nodePositions.get(transform.startNodeId);
            const end = nodePositions.get(transform.endNodeId);
            if (!start || !end) return;
            const startPosition = start.clone();
            const endPosition = end.clone();
            const componentId = component.userData.componentId as number;
            const tension = tensionByElementKey.get(`${componentId}:${transform.elementId}`);
            if (tension !== undefined) maximumComponentTension = Math.max(maximumComponentTension, tension);

            const direction = endPosition.clone().sub(startPosition);
            const length = direction.length();
            if (length <= 1e-8) return;
            const quaternion = new THREE.Quaternion().setFromUnitVectors(up, direction.normalize());
            const midpoint = startPosition.add(endPosition).multiplyScalar(0.5);
            transform.mid.copy(midpoint);
            transform.quat.copy(quaternion);
            transform.length = length;
            scale.set(transform.baseScaleX, length, transform.baseScaleZ);
            matrix.compose(midpoint, quaternion, scale);
            instancedMesh.setMatrixAt(index, matrix);
        });
        positions.needsUpdate = true;
        instancedMesh.instanceMatrix.needsUpdate = true;
        instancedMesh.computeBoundingSphere();
        lineSegments.geometry.computeBoundingSphere();
        const originalColor = component.userData.originalColor as THREE.Color | undefined;
        if (originalColor && (component.userData.aquaSimType === 'truss' || maximumComponentTension > 0)) {
            const color = tensionByElementKey.size === 0
                ? originalColor
                : new THREE.Color().setHSL(
                    0.66 * (1 - (maximumTensionN > 0 ? Math.min(1, maximumComponentTension / maximumTensionN) : 0)),
                    0.9,
                    0.52
                );
            if (instancedMesh.material instanceof THREE.Material && 'color' in instancedMesh.material) {
                (instancedMesh.material as THREE.MeshStandardMaterial).color.copy(color);
            }
            if (lineSegments.material instanceof THREE.Material && 'color' in lineSegments.material) {
                (lineSegments.material as THREE.LineBasicMaterial).color.copy(color);
            }
        }
    }
}
