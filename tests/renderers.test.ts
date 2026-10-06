import { describe, it, expect } from 'vitest';
import * as THREE from 'three';
import { parseAmodelXml } from '@/parser/amodelParser';
import { createBeamGroup, updateBeamScale } from '@/viewer/renderers/beamRenderer';
import { createTrussGroup, updateTrussScale } from '@/viewer/renderers/trussRenderer';
import { createMembraneGroup } from '@/viewer/renderers/membraneRenderer';
import { updateLineState } from '@/viewer/renderers/lineRenderer';
import { AquaSimViewer } from '@/viewer/AquaSimViewer';
import { aquaSimToThree } from '@/viewer/cameraUtils';

describe('AquaSim 3D Renderers and Geometry', () => {
    it.each([
        ['beam', createBeamGroup, updateBeamScale],
        ['truss', createTrussGroup, updateTrussScale]
    ] as const)('keeps %s geometry independent and scales only cross-sections', (type, create, update) => {
        const model = parseAmodelXml(`<model><Nodes>
            <node id="1" x="0" y="0" z="0"/>
            <node id="2" x="0" y="10" z="0"/>
            </Nodes><Components><${type} id="7" name="Line">
            <element id="1" StartNode_ID="1" EndNode_ID="2"/>
            </${type}></Components></model>`);
        const components = type === 'beam' ? model.beams : model.trusses;
        const first = create(components, model.nodes, { renderMode: 'wireframe' });
        const second = create(components, model.nodes);
        const mesh = first.children[0].userData.instancedMesh as THREE.InstancedMesh;
        const other = second.children[0].userData.instancedMesh as THREE.InstancedMesh;
        expect(mesh.geometry).not.toBe(other.geometry);
        expect(mesh.visible).toBe(false);
        expect(first.children[0].userData.lineSegments.visible).toBe(true);

        const matrix = new THREE.Matrix4();
        mesh.getMatrixAt(0, matrix);
        const initial = new THREE.Vector3().setFromMatrixScale(matrix);
        update(first, 3);
        mesh.getMatrixAt(0, matrix);
        const scaled = new THREE.Vector3().setFromMatrixScale(matrix);
        expect(scaled.x).toBeCloseTo(initial.x * 3);
        expect(scaled.y).toBeCloseTo(10);
        expect(scaled.z).toBeCloseTo(initial.z * 3);
        expect(new THREE.Vector3().setFromMatrixPosition(matrix).toArray()).toEqual([0, 5, 0]);
    });

    it('renders mixed triangular and quadrilateral membranes while rejecting missing node references', () => {
        const model = parseAmodelXml(`<model name="MixedMembranes">
            <Nodes>
                <node id="1" x="0" y="0" z="0"/>
                <node id="2" x="10" y="0" z="0"/>
                <node id="3" x="10" y="10" z="0"/>
                <node id="4" x="0" y="10" z="0"/>
            </Nodes>
            <Components><membrane id="1" name="Mixed">
                <element id="1" nodeA="1" nodeB="2" nodeC="3"/>
                <element id="2" nodeA="1" nodeB="2" nodeC="3" nodeD="4"/>
                <element id="3" nodeA="1" nodeB="2" nodeC="999"/>
                <element id="4" nodeA="1" nodeB="2" nodeC="3" nodeD="999"/>
            </membrane></Components>
        </model>`);

        expect(model.membranes[0].elements).toHaveLength(2);
        expect(model.membranes[0].elements[0].nodeD).toBeUndefined();
        expect(model.report.invalidReferences).toBe(2);
        expect(model.report.totalElementCount).toBe(2);

        const group = createMembraneGroup(model.membranes, model.nodes);
        const component = group.children[0] as THREE.Group;
        const solid = component.children[0] as THREE.Mesh;
        const wire = component.children[1] as THREE.Mesh;
        expect(wire.geometry).toBe(solid.geometry);
        expect(solid.geometry.getAttribute('position').count).toBe(9);
        expect(Array.from(solid.geometry.getAttribute('position').array).slice(0, 9))
            .toEqual([0, 0, 0, 10, 0, 0, 10, 10, 0]);
        const normals = solid.geometry.getAttribute('normal');
        for (let i = 0; i < normals.count; i++) {
            expect(normals.getX(i)).toBeCloseTo(0);
            expect(normals.getY(i)).toBeCloseTo(0);
            expect(normals.getZ(i)).toBeCloseTo(1);
        }
    });

    it('should preserve AquaSim coordinates in aquaSimToThree without flipping axes', () => {
        const v = aquaSimToThree(12.5, -34.2, 56.7);
        expect(v.x).toBe(12.5);
        expect(v.y).toBe(-34.2);
        expect(v.z).toBe(56.7);
    });

    it('updates line elements from dynamic node positions', () => {
        const model = parseAmodelXml(`<model><Nodes>
            <node id="1" x="0" y="0" z="0"/>
            <node id="2" x="0" y="0" z="-10"/>
            </Nodes><Components><truss id="1" name="winch1">
            <element id="1" StartNode_ID="1" EndNode_ID="2"/>
            </truss></Components></model>`);
        const group = createTrussGroup(model.trusses, model.nodes);
        updateLineState(group, new Map([
            [1, new THREE.Vector3(0, 0, 0)],
            [2, new THREE.Vector3(0, 0, -9)]
        ]));
        const line = group.children[0].userData.lineSegments as THREE.LineSegments;
        const positions = line.geometry.getAttribute('position');
        expect(positions.getZ(0)).toBe(0);
        expect(positions.getZ(1)).toBe(-9);
        const instances = group.children[0].userData.instancedMesh as THREE.InstancedMesh;
        const matrix = new THREE.Matrix4();
        instances.getMatrixAt(0, matrix);
        const midpoint = new THREE.Vector3().setFromMatrixPosition(matrix);
        expect(midpoint.z).toBe(-4.5);
    });

    it('keeps excluded membrane dynamics out of winch result playback', () => {
        const model = parseAmodelXml(`<model><Nodes>
            <node id="1" x="0" y="0" z="0"/>
            <node id="2" x="1" y="0" z="-1"/>
            <node id="3" x="1" y="1" z="-1"/>
            </Nodes><Components><membrane id="1" name="Net">
            <element id="1" nodeA="1" nodeB="2" nodeC="3"/>
            </membrane></Components></model>`);
        const viewer = Object.create(AquaSimViewer.prototype) as AquaSimViewer;
        const membraneGroup = createMembraneGroup(model.membranes, model.nodes);
        Object.assign(viewer, {
            modelGroup: new THREE.Group(),
            beamGroup: new THREE.Group(),
            trussGroup: new THREE.Group(),
            membraneGroup,
            currentNodes: model.nodes
        });

        viewer.applyWinchFrame(
            new Map([
                [1, new THREE.Vector3(0, 0, 0)],
                [2, new THREE.Vector3(1, 0, 0)],
                [3, new THREE.Vector3(1, 1, 0)]
            ]),
            new Map(),
            0
        );
        const mesh = membraneGroup.children[0].children[0] as THREE.Mesh;
        let positions = mesh.geometry.getAttribute('position');
        expect(positions.getZ(0)).toBe(0);
        expect(positions.getZ(1)).toBe(-1);
        expect(positions.getZ(2)).toBe(-1);

        viewer.clearWinchFrame();
        positions = mesh.geometry.getAttribute('position');
        expect(positions.getZ(0)).toBe(0);
        expect(positions.getZ(1)).toBe(-1);
    });

    it('should create 3D solid cross-sections for circular and profile beams from amodel', () => {
        const xml = `<?xml version="1.0" encoding="UTF-8"?>
        <model name="BeamModel">
            <Nodes>
                <node id="1" x="0" y="0" z="0"/>
                <node id="2" x="10" y="0" z="0"/>
                <node id="3" x="20" y="0" z="0"/>
            </Nodes>
            <Components>
                <beam id="1" name="Flytekrage_Ø500">
                    <wizard type="circular" outerDiameter="500.0"/>
                    <elements>
                        <element id="101" StartNode_ID="1" EndNode_ID="2"/>
                        <element id="102" StartNode_ID="2" EndNode_ID="3"/>
                    </elements>
                </beam>
                <beam id="2" name="Klammer_H500">
                    <wizard type="ibeam" tfw="250.0" wh="410.0" tft="20.0" bft="20.0"/>
                    <elements>
                        <element id="103" StartNode_ID="1" EndNode_ID="2"/>
                    </elements>
                </beam>
            </Components>
        </model>`;

        const model = parseAmodelXml(xml);
        expect(model.beams[0].section?.shape).toBe('circular');
        expect(model.beams[0].section?.radius).toBeCloseTo(0.25);
        expect(model.beams[1].section?.shape).toBe('ibeam');
        expect(model.beams[1].section?.width).toBeCloseTo(0.25);
        expect(model.beams[1].section?.height).toBeCloseTo(0.45);

        const beamGroup = createBeamGroup(model.beams, model.nodes, { scaleFactor: 1.0 });
        expect(beamGroup.children.length).toBe(2);

        const comp1 = beamGroup.children[0] as THREE.Group;
        expect(comp1.userData.aquaSimType).toBe('beam');
        expect(comp1.userData.elementCount).toBe(2);
        expect(comp1.userData.instancedMesh).toBeDefined();

        const im1 = comp1.userData.instancedMesh as THREE.InstancedMesh;
        expect(im1.count).toBe(2);

        // Test dynamic scaling
        updateBeamScale(beamGroup, 2.5);
    });

    it('should create circular 3D cylindrical sections for trusses with dynamic scaling', () => {
        const xml = `<?xml version="1.0" encoding="UTF-8"?>
        <model name="TrussModel">
            <Nodes>
                <node id="1" x="0" y="0" z="0"/>
                <node id="2" x="0" y="10" z="-50"/>
            </Nodes>
            <Components>
                <truss id="2" name="Omegakjetting 22 mm">
                    <elements>
                        <element id="201" StartNode_ID="1" EndNode_ID="2"/>
                    </elements>
                </truss>
            </Components>
        </model>`;

        const model = parseAmodelXml(xml);
        expect(model.trusses[0].section?.shape).toBe('circular');
        expect(model.trusses[0].section?.radius).toBeCloseTo(0.011); // 22mm dia -> 11mm radius

        const trussGroup = createTrussGroup(model.trusses, model.nodes, { scaleFactor: 1.0 });
        expect(trussGroup.children.length).toBe(1);

        const comp = trussGroup.children[0] as THREE.Group;
        expect(comp.userData.aquaSimType).toBe('truss');
        expect(comp.userData.instancedMesh).toBeDefined();

        const im = comp.userData.instancedMesh as THREE.InstancedMesh;
        expect(im.count).toBe(1);

        // Test dynamic scaling
        updateTrussScale(trussGroup, 5.0);
    });

    it('should triangulate quadrilateral Membrane elements into 2 triangles per quad', () => {
        const xml = `<?xml version="1.0" encoding="UTF-8"?>
        <model name="MembraneModel">
            <Nodes>
                <node id="1" x="0" y="0" z="0"/>
                <node id="2" x="10" y="0" z="0"/>
                <node id="3" x="10" y="10" z="0"/>
                <node id="4" x="0" y="10" z="0"/>
            </Nodes>
            <Components>
                <membrane id="3" name="NetMesh">
                    <elements>
                        <element id="301" nodeA="1" nodeB="2" nodeC="3" nodeD="4"/>
                    </elements>
                </membrane>
            </Components>
        </model>`;

        const model = parseAmodelXml(xml);
        const membraneGroup = createMembraneGroup(model.membranes, model.nodes);

        expect(membraneGroup.children.length).toBe(1);
        const compGroup = membraneGroup.children[0] as THREE.Group;
        expect(compGroup.userData.aquaSimType).toBe('membrane');
        expect(compGroup.userData.componentName).toBe('NetMesh');
        expect(compGroup.userData.elementCount).toBe(1);

        const solidMesh = compGroup.children[0] as THREE.Mesh;
        expect(solidMesh).toBeInstanceOf(THREE.Mesh);

        // 1 quad = 2 triangles = 6 vertices = 18 floats in BufferAttribute
        const posAttr = solidMesh.geometry.getAttribute('position');
        expect(posAttr.count).toBe(6);

        // Check material side is DoubleSide and depthWrite is false
        const mat = solidMesh.material as THREE.MeshStandardMaterial;
        expect(mat.side).toBe(THREE.DoubleSide);
        expect(mat.transparent).toBe(true);
        expect(mat.depthWrite).toBe(false);
    });
});
