import type { ComponentProperties, ModelProperties } from './controls';

const G = 9.80665;
const WATER_DENSITY = 1025;
function number(element: Element | null, name: string, fallback?: number): number {
    const raw = element?.getAttribute(name);
    const value = raw === null || raw === undefined || raw.trim() === '' ? NaN : Number(raw);
    if (Number.isFinite(value)) return value;
    if (fallback !== undefined) return fallback;
    throw new Error(`Missing or invalid ${name} in ${element?.tagName ?? 'material'}.`);
}

/** Read effective SI properties without requiring a running simulation service. */
export function readModelProperties(xml: string): ModelProperties {
    const document = new DOMParser().parseFromString(xml, 'application/xml');
    if (document.querySelector('parsererror')) throw new Error('The model file contains invalid XML.');
    const nodes = new Map<number, number[]>();
    for (const node of document.querySelectorAll('Nodes > node')) {
        nodes.set(number(node, 'id'), ['x','y','z'].map(axis => number(node, axis)));
    }
    const root = document.querySelector('Components');
    if (!root) throw new Error('The model file has no Components section.');
    const components: ComponentProperties[] = [];
    const winches: ModelProperties['winches'] = [];
    for (const component of Array.from(root.children)) {
        const type = component.tagName;
        if (type !== 'beam' && type !== 'truss' && type !== 'membrane') continue;
        const componentId = number(component, 'id');
        const name = component.getAttribute('name') || type;
        const edges: Array<{ a: number; b: number; length: number }> = [];
        const seen = new Set<string>();
        const add = (a: number, b: number): void => {
            const first = nodes.get(a), second = nodes.get(b);
            if (!first || !second) throw new Error(`${name} references a missing node.`);
            const length = Math.hypot(...first.map((position, i) => position - second[i]));
            if (length <= 1e-9) throw new Error(`${name} contains a zero-length segment.`);
            edges.push({ a, b, length });
        };
        for (const element of component.querySelectorAll('element')) {
            if (type !== 'membrane') {
                add(number(element, 'StartNode_ID', number(element, 'startNodeId', NaN)),
                    number(element, 'EndNode_ID', number(element, 'endNodeId', NaN)));
            } else {
                const ids = ['nodeA','nodeB','nodeC','nodeD'].map(key => number(element, key,
                    number(element, key[0].toUpperCase()+key.slice(1), NaN))).filter(Number.isFinite);
                for (let i = 0; i < ids.length; i++) for (let j = i+1; j < ids.length; j++) {
                    const key = [ids[i], ids[j]].sort((a,b) => a-b).join(':');
                    if (!seen.has(key)) { seen.add(key); add(ids[i], ids[j]); }
                }
            }
        }
        const lengthM = edges.reduce((sum, edge) => sum+edge.length, 0);
        let eaN: number | null = null, air: number, water: number;
        if (type === 'truss') {
            const material = component.querySelector('mooring');
            const area = number(material, 'areal');
            eaN = number(material, 'young') * area;
            air = number(material, 'weightInAir', number(material, 'massdensity', NaN)*area);
            water = number(material, 'weightInWater', air-WATER_DENSITY*area);
        } else if (type === 'membrane') {
            const area = number(component, 'areal', 2.5e-6) || 2.5e-6;
            const density = number(component, 'massDensity', 1000) || 1000;
            eaN = (number(component, 'emodule', 1e9) || 1e9)*area;
            air = density*area; water = (density-WATER_DENSITY)*area;
        } else {
            const material = component.querySelector('materials');
            const wizard = component.querySelector('wizard');
            let materialArea = NaN, displacedArea = number(material, 'crossectionvolume', NaN);
            if (wizard?.getAttribute('type') === 'circular') {
                const diameter = number(wizard, 'outerDiameter')/1000;
                const wall = number(wizard, 'thickness')/1000;
                materialArea = Math.PI*(diameter**2-(diameter-2*wall)**2)/4;
                if (!Number.isFinite(displacedArea)) displacedArea = Math.PI*diameter**2/4;
            } else if (wizard?.getAttribute('type') === 'ibeam') {
                materialArea = (number(wizard,'tfw')*(number(wizard,'tft')+number(wizard,'bft'))
                    +number(wizard,'wh')*number(wizard,'wt'))/1e6;
                if (!Number.isFinite(displacedArea)) displacedArea = materialArea;
            }
            air = number(material,'weightInAir',materialArea*number(wizard,'massDensity',NaN));
            water = number(material,'weightInWater',air === 0 ? 0 : air-WATER_DENSITY*displacedArea);
        }
        if (!Number.isFinite(air) || !Number.isFinite(water) || (eaN !== null && (!Number.isFinite(eaN) || eaN <= 0))) {
            throw new Error(`${name}: the model is missing valid simulation material properties.`);
        }
        const row: ComponentProperties = { componentId,name,type,eaN,lengthM,massKg:air*lengthM,
            weightInAirN:air*lengthM*G,weightInWaterN:water*lengthM*G };
        if (type === 'truss' && /^winch[12]$/i.test(name.trim())) {
            const remaining = new Set(edges.map((_, i) => i));
            const paths: number[] = [];
            const adjacency = new Map<number, number[]>();
            edges.forEach((edge,i) => { for (const node of [edge.a,edge.b]) adjacency.set(node,[...(adjacency.get(node) ?? []),i]); });
            while (remaining.size) {
                const pending = [remaining.values().next().value!];
                let length = 0;
                while (pending.length) {
                    const i = pending.pop()!;
                    if (!remaining.delete(i)) continue;
                    length += edges[i].length;
                    for (const node of [edges[i].a,edges[i].b]) for (const neighbor of adjacency.get(node)!) {
                        if (remaining.has(neighbor)) pending.push(neighbor);
                    }
                }
                paths.push(length);
            }
            row.pathLengthsM = paths;
            winches.push({ componentId,name,pathLengthsM:paths });
        }
        components.push(row);
    }
    if (!components.length) throw new Error('The model file contains no supported components.');
    return { components,winches };
}

export function readModelFile(file: File): Promise<string> {
    if (typeof file.text === 'function') return file.text();
    return new Promise((resolve,reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result));
        reader.onerror = () => reject(reader.error ?? new Error('Unable to read model file.'));
        reader.readAsText(file);
    });
}
