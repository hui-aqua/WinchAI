import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { readModelProperties } from '@/simulation/modelProperties';

describe('amodel simulation properties', () => {
    it('loads the project model directly without an API', () => {
        const properties = readModelProperties(readFileSync('models/Next_Otervika_NLS3.amodel', 'utf8'));
        expect(properties.components.length).toBeGreaterThan(0);
        expect(properties.winches).toHaveLength(2);
        for (const component of properties.components) {
            expect(Number.isFinite(component.massKg)).toBe(true);
            expect(Number.isFinite(component.weightInWaterN)).toBe(true);
            expect(component.weightInAirN).toBeCloseTo(component.massKg*9.80665);
        }
    });
    it('derives pipe mass and displaced water using millimetre dimensions', () => {
        const properties = readModelProperties(`<model><Nodes><node id="1" x="0" y="0" z="0"/><node id="2" x="2" y="0" z="0"/></Nodes><Components>
        <beam id="1"><wizard type="circular" outerDiameter="100" thickness="10" massDensity="1000"/><element StartNode_ID="1" EndNode_ID="2"/></beam></Components></model>`);
        expect(properties.components[0].massKg).toBeCloseTo(Math.PI*(.1**2-.08**2)/4*1000*2);
        expect(properties.components[0].weightInWaterN).toBeLessThan(0);
    });
});
