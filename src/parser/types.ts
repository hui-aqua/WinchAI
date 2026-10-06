export interface AquaSimNode {
    id: number;
    x: number;
    y: number;
    z: number;
    metadata?: Record<string, string>;
}

export interface LineElement {
    id: number;
    startNodeId: number;
    endNodeId: number;
    metadata?: Record<string, string>;
}

export interface MembraneElement {
    id: number;
    nodeA: number;
    nodeB: number;
    nodeC: number;
    nodeD?: number;
    metadata?: Record<string, string>;
}

export interface ComponentColor {
    r: number;
    g: number;
    b: number;
}

export interface ElementSection {
    shape: 'circular' | 'ibeam' | 'rectangular';
    radius?: number; // in meters
    width?: number;  // in meters
    height?: number; // in meters
    outerDiameter?: number; // in meters
}

export interface StructuralComponent<T> {
    id?: number;
    name: string;
    type: 'beam' | 'truss' | 'membrane';
    color?: ComponentColor;
    section?: ElementSection;
    elements: T[];
    metadata?: Record<string, string>;
}

export interface ParseReport {
    nodeCount: number;
    beamComponentCount: number;
    beamElementCount: number;
    trussComponentCount: number;
    trussElementCount: number;
    membraneComponentCount: number;
    membraneElementCount: number;
    totalElementCount: number;
    invalidReferences: number;
    warnings: string[];
}

export interface AquaSimModel {
    name?: string;
    description?: string;
    version?: string;
    nodes: Map<number, AquaSimNode>;
    beams: StructuralComponent<LineElement>[];
    trusses: StructuralComponent<LineElement>[];
    membranes: StructuralComponent<MembraneElement>[];
    boundingBox: {
        min: { x: number; y: number; z: number };
        max: { x: number; y: number; z: number };
    };
    report: ParseReport;
    metadata?: Record<string, string>;
}
