import { describe, it, expect } from 'vitest';
import { parseAmodelXml } from '@/parser/amodelParser';
import * as fs from 'fs';
import * as path from 'path';

describe('AquaSim Model Parser', () => {
    it('should parse nodes with non-sequential IDs correctly', () => {
        const xml = `<?xml version="1.0" encoding="UTF-8"?>
        <model name="TestModel">
            <Nodes>
                <node id="100" x="1.2" y="2.3" z="-4.5"/>
                <node id="505" x="10.0" y="-20.5" z="30.2"/>
            </Nodes>
        </model>`;

        const model = parseAmodelXml(xml);
        expect(model.nodes.size).toBe(2);

        const n100 = model.nodes.get(100);
        expect(n100).toBeDefined();
        expect(n100?.x).toBe(1.2);
        expect(n100?.y).toBe(2.3);
        expect(n100?.z).toBe(-4.5);

        const n505 = model.nodes.get(505);
        expect(n505).toBeDefined();
        expect(n505?.x).toBe(10.0);
        expect(n505?.y).toBe(-20.5);
        expect(n505?.z).toBe(30.2);

        expect(model.boundingBox.min).toEqual({ x: 1.2, y: -20.5, z: -4.5 });
        expect(model.boundingBox.max).toEqual({ x: 10.0, y: 2.3, z: 30.2 });
    });

    it('should parse beam connectivity correctly', () => {
        const xml = `<?xml version="1.0" encoding="UTF-8"?>
        <model name="BeamTest">
            <Nodes>
                <node id="1" x="0" y="0" z="0"/>
                <node id="2" x="1" y="2" z="3"/>
            </Nodes>
            <Components>
                <beam id="10" name="MainBeam">
                    <elements>
                        <element id="1" StartNode_ID="1" EndNode_ID="2"/>
                    </elements>
                </beam>
            </Components>
        </model>`;

        const model = parseAmodelXml(xml);
        expect(model.beams.length).toBe(1);
        expect(model.beams[0].name).toBe('MainBeam');
        expect(model.beams[0].elements.length).toBe(1);
        expect(model.beams[0].elements[0].startNodeId).toBe(1);
        expect(model.beams[0].elements[0].endNodeId).toBe(2);
        expect(model.report.invalidReferences).toBe(0);
    });

    it('should parse truss connectivity correctly', () => {
        const xml = `<?xml version="1.0" encoding="UTF-8"?>
        <model name="TrussTest">
            <Nodes>
                <node id="10" x="0" y="0" z="0"/>
                <node id="20" x="5" y="5" z="5"/>
            </Nodes>
            <Components>
                <truss id="20" name="MooringRope">
                    <element id="99" StartNode_ID="10" EndNode_ID="20"/>
                </truss>
            </Components>
        </model>`;

        const model = parseAmodelXml(xml);
        expect(model.trusses.length).toBe(1);
        expect(model.trusses[0].name).toBe('MooringRope');
        expect(model.trusses[0].elements[0].startNodeId).toBe(10);
        expect(model.trusses[0].elements[0].endNodeId).toBe(20);
    });

    it('should parse membrane connectivity correctly', () => {
        const xml = `<?xml version="1.0" encoding="UTF-8"?>
        <model name="MembraneTest">
            <Nodes>
                <node id="1" x="0" y="0" z="0"/>
                <node id="2" x="1" y="0" z="0"/>
                <node id="3" x="1" y="1" z="0"/>
                <node id="4" x="0" y="1" z="0"/>
            </Nodes>
            <Components>
                <membrane id="30" name="NetPanel">
                    <element id="701" nodeA="1" nodeB="2" nodeC="3" nodeD="4"/>
                </membrane>
            </Components>
        </model>`;

        const model = parseAmodelXml(xml);
        expect(model.membranes.length).toBe(1);
        expect(model.membranes[0].name).toBe('NetPanel');
        expect(model.membranes[0].elements.length).toBe(1);
        const elem = model.membranes[0].elements[0];
        expect(elem.nodeA).toBe(1);
        expect(elem.nodeB).toBe(2);
        expect(elem.nodeC).toBe(3);
        expect(elem.nodeD).toBe(4);
    });

    it('should parse component color from <color red="..." green="..." blue="..."/>', () => {
        const xml = `<?xml version="1.0" encoding="UTF-8"?>
        <model name="ColorTest">
            <Nodes>
                <node id="1" x="0" y="0" z="0"/>
                <node id="2" x="1" y="0" z="0"/>
            </Nodes>
            <Components>
                <truss id="16" name="LB_Roundsling 1.5 m">
                    <description active="false">
                        <color red="0.0" green="0.6" blue="1.0" />
                    </description>
                    <elements>
                        <element id="4775" StartNode_ID="1" EndNode_ID="2"/>
                    </elements>
                </truss>
            </Components>
        </model>`;

        const model = parseAmodelXml(xml);
        expect(model.trusses[0].color).toBeDefined();
        expect(model.trusses[0].color?.r).toBe(0.0);
        expect(model.trusses[0].color?.g).toBe(0.6);
        expect(model.trusses[0].color?.b).toBe(1.0);
    });

    it('should gracefully handle broken node references without crashing', () => {
        const xml = `<?xml version="1.0" encoding="UTF-8"?>
        <model name="BrokenRefTest">
            <Nodes>
                <node id="1" x="0" y="0" z="0"/>
            </Nodes>
            <Components>
                <beam id="1" name="BrokenBeam">
                    <element id="1" StartNode_ID="1" EndNode_ID="999"/>
                </beam>
                <membrane id="2" name="BrokenMembrane">
                    <element id="2" nodeA="1" nodeB="2" nodeC="3" nodeD="4"/>
                </membrane>
            </Components>
        </model>`;

        const model = parseAmodelXml(xml);
        expect(model.beams[0].elements.length).toBe(0);
        expect(model.membranes[0].elements.length).toBe(0);
        expect(model.report.invalidReferences).toBe(2);
        expect(model.report.warnings.length).toBe(2);
    });

    it('should parse real ENCC100323640.amodel accurately', () => {
        const filePath = path.resolve(__dirname, './fixtures/ENCC100323640.amodel');
        const content = fs.readFileSync(filePath, 'utf-8');

        const model = parseAmodelXml(content);
        expect(model.nodes.size).toBe(3755);
        expect(model.beams.length).toBe(3);
        expect(model.trusses.length).toBe(11);
        expect(model.membranes.length).toBe(2);
        expect(model.report.totalElementCount).toBe(5020);
        expect(model.membranes.flatMap(component => component.elements)
            .filter(element => element.nodeD === undefined)).toHaveLength(72);
        expect(model.report.invalidReferences).toBe(0);

        // Bounding box checks (35m radius circular cage, 40.8m depth)
        expect(model.boundingBox.min.x).toBeCloseTo(-35.0);
        expect(model.boundingBox.max.x).toBeCloseTo(35.0);
        expect(model.boundingBox.min.y).toBeCloseTo(-35.0);
        expect(model.boundingBox.max.y).toBeCloseTo(35.0);
        expect(model.boundingBox.min.z).toBeCloseTo(-40.8);
        expect(model.boundingBox.max.z).toBeCloseTo(0.0);
    });
});
