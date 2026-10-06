import { describe, it, expect, vi } from 'vitest';
import * as THREE from 'three';
import { AquaSimViewer } from '@/viewer/AquaSimViewer';
import { parseAmodelXml } from '@/parser/amodelParser';

describe('single-model scene', () => {
    it('applies dynamic node positions and colors winch lines by element tension', () => {
        const viewer = Object.create(AquaSimViewer.prototype) as AquaSimViewer;
        const originalColor = new THREE.Color(0x00aaff);
        const material = new THREE.MeshStandardMaterial({ color: originalColor });
        const lineMaterial = new THREE.LineBasicMaterial({ color: originalColor });
        const instancedMesh = new THREE.InstancedMesh(new THREE.BoxGeometry(), material, 1);
        const lineSegments = new THREE.LineSegments(new THREE.BufferGeometry(), lineMaterial);
        lineSegments.geometry.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 0, 0, 1], 3));
        const component = new THREE.Group();
        component.userData = {
            componentName: 'winch1',
            componentId: 1,
            aquaSimType: 'truss',
            originalColor: originalColor.clone(),
            transforms: [{
                mid: new THREE.Vector3(0, 0, 0.5),
                quat: new THREE.Quaternion(),
                length: 1,
                baseScaleX: 1,
                baseScaleZ: 1,
                startNodeId: 1,
                endNodeId: 2,
                elementId: 7
            }],
            lineNodeIds: [1, 2],
            instancedMesh,
            lineSegments
        };
        const trussGroup = new THREE.Group();
        trussGroup.add(component);
        Object.assign(viewer, {
            trussGroup,
            beamGroup: new THREE.Group(),
            membraneGroup: new THREE.Group(),
            currentNodes: new Map()
        });

        viewer.applyWinchFrame(
            new Map([[1, new THREE.Vector3(0, 0, 0)], [2, new THREE.Vector3(0, 0, 2)]]),
            new Map([['1:7', 10]]),
            10
        );
        expect(lineSegments.geometry.getAttribute('position').getZ(1)).toBe(2);
        expect(material.color.r).toBeGreaterThan(0.9);
        expect(material.color.g).toBeLessThan(0.2);
        expect(lineMaterial.color.equals(material.color)).toBe(true);

        viewer.clearWinchFrame();
        expect(material.color.equals(originalColor)).toBe(true);
    });

    it('disposes shared membrane resources once when clearing a model', () => {
        const viewer = Object.create(AquaSimViewer.prototype) as AquaSimViewer;
        const geometry = new THREE.BufferGeometry();
        const material = new THREE.MeshBasicMaterial();
        const modelGroup = new THREE.Group();
        modelGroup.add(new THREE.Mesh(geometry, material), new THREE.Mesh(geometry, material));
        const componentObjectMap = new Map([['membrane_1', modelGroup.children[0]]]);
        const currentBounds = new THREE.Box3(new THREE.Vector3(), new THREE.Vector3(1, 1, 1));
        Object.assign(viewer, { modelGroup, componentObjectMap, currentBounds });
        const disposeGeometry = vi.spyOn(geometry, 'dispose');
        const disposeMaterial = vi.spyOn(material, 'dispose');
        viewer.clearModel();
        expect(disposeGeometry).toHaveBeenCalledOnce();
        expect(disposeMaterial).toHaveBeenCalledOnce();
        expect(modelGroup.children).toHaveLength(0);
        expect(componentObjectMap.size).toBe(0);
        expect(currentBounds.isEmpty()).toBe(true);
    });

    it('releases observers, controls, helpers, and canvas once on destruction', () => {
        const viewer = Object.create(AquaSimViewer.prototype) as AquaSimViewer;
        const container = document.createElement('div');
        const canvas = document.createElement('canvas');
        container.append(canvas);
        const disposable = () => ({ dispose: vi.fn() });
        const controls = disposable();
        const axesHelper = disposable();
        const grids = [disposable(), disposable(), disposable()];
        const renderer = { ...disposable(), domElement: canvas };
        const resizeObserver = { disconnect: vi.fn() };
        Object.assign(viewer, {
            modelGroup: new THREE.Group(), componentObjectMap: new Map(),
            currentBounds: new THREE.Box3(), animFrameId: null, isDestroyed: false,
            controls, axesHelper, renderer, resizeObserver,
            gridHelperXY: grids[0], gridHelperXZ: grids[1], gridHelperYZ: grids[2]
        });
        viewer.destroy();
        viewer.destroy();
        expect(resizeObserver.disconnect).toHaveBeenCalledOnce();
        for (const resource of [controls, axesHelper, renderer, ...grids]) {
            expect(resource.dispose).toHaveBeenCalledOnce();
        }
        expect(container.children).toHaveLength(0);
    });

    it('disposes and replaces the previous geometry without shifting the new model', () => {
        const viewer = Object.create(AquaSimViewer.prototype) as AquaSimViewer;
        const modelGroup = new THREE.Group();
        Object.assign(viewer, { modelGroup, componentObjectMap: new Map(), currentBounds: new THREE.Box3(), currentScaleFactor: 1, renderMode: 'both', axesHelper: new THREE.AxesHelper(), setupReferenceGridPlanes: vi.fn(), fitView: vi.fn(), gridVisible: true });
        const model = parseAmodelXml('<model><Nodes><node id="1" x="0" y="0" z="0"/><node id="2" x="10" y="0" z="0"/></Nodes><Components><beam id="1" name="Beam"><elements><element id="1" StartNode_ID="1" EndNode_ID="2"/></elements></beam></Components></model>');
        viewer.loadModel(model);
        const previous = modelGroup.children[0];
        let geometry: THREE.BufferGeometry | undefined;
        previous.traverse(object => { if ((object as THREE.Mesh).geometry) geometry = (object as THREE.Mesh).geometry; });
        expect(geometry).toBeDefined();
        const dispose = vi.spyOn(geometry!, 'dispose');
        viewer.loadModel(model);
        expect(dispose).toHaveBeenCalled();
        expect(modelGroup.children).toHaveLength(3);
        expect(modelGroup.children).not.toContain(previous);
        expect(modelGroup.children[0].position.x).toBe(0);
    });
});
