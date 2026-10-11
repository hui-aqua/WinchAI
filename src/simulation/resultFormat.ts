import type { ComponentProperties } from './controls';

export interface WinchResultHeader {
    schema: 'aquasim-winch-result';
    version: 2;
    modelName: string;
    nodeIds: number[];
    nodePositionsM: number[][];
    surfaceNodeIds: number[];
    fixedNodeIds: number[];
    ropeElements: Array<{ componentId: number; componentName: string; elementId: number }>;
    frameCount: number;
    frameRecordBytes: number;
    settings: {
        takeUpSpeedMPerMin: number;
        durationSeconds: number;
        frameIntervalSeconds: number;
        integrationStepSeconds: number;
        internalSteps: number;
        waterDensityKgM3: number;
        payoutM?: number;
        winchPayoutsM?: Record<string, number>;
        initialIncrementM?: number;
        minimumIncrementM?: number;
        acceptedSteps?: number;
        rejectedSteps?: number;
        waterDepthM?: number;
        membraneDynamics: boolean;
        solverMethod?: string;
        hydrodynamicDrag: boolean;
        addedMass: boolean;
    };
    convergence?: Array<{
        forceResidualN: number;
        momentResidualNm: number;
        displacementM: number;
        tensionChangeN: number;
        buoyancyChangeN: number;
        constraintErrorM: number;
        incrementM?: number;
        event?: boolean;
    }>;
    componentProperties?: ComponentProperties[];
    beamLoads: Array<{
        componentId: number;
        name: string;
        lengthM: number;
        massKg: number;
        weightN: number;
        buoyancyN: number;
        centerM: number[];
    }>;
    winches: Array<{ name: string; componentId: number; topNodeIds: number[] }>;
    centerOfMassM: number[];
    totalMassKg: number;
    weightN: number;
    buoyancyN: number;
    netVerticalLoadN: number;
    momentNm: number[];
    warnings: string[];
    assumptions: string[];
}

export interface WinchResultFrame {
    timeS: number;
    takeUpM: number;
    translationZM: number;
    winch1TensionN: number;
    winch2TensionN: number;
    netVerticalLoadN: number;
    momentXNm: number;
    momentYNm: number;
    momentZNm: number;
    nodePositionsM: Float32Array;
    ropeTensionsN: Float32Array;
}

export interface WinchResult {
    header: WinchResultHeader;
    frames: WinchResultFrame[];
}

const MAGIC = 'AQWR';
const VERSION = 2;
const HEADER_PREFIX_BYTES = 10;
const FRAME_SUMMARY_BYTES = 9 * Float64Array.BYTES_PER_ELEMENT;
const NODE_POSITION_BYTES = 3 * Float32Array.BYTES_PER_ELEMENT;
const TENSION_BYTES = Float32Array.BYTES_PER_ELEMENT;

export function parseWinchResult(buffer: ArrayBuffer): WinchResult {
    if (buffer.byteLength < HEADER_PREFIX_BYTES) {
        throw new Error('The result file is truncated or is not an AquaSim winch result.');
    }

    const view = new DataView(buffer);
    const magic = String.fromCharCode(...new Uint8Array(buffer, 0, 4));
    if (magic !== MAGIC) throw new Error('Unrecognized result file. Expected AQWR binary format.');

    const version = view.getUint16(4, true);
    if (version !== VERSION) throw new Error(`Unsupported result version ${version}. Expected ${VERSION}.`);

    const headerLength = view.getUint32(6, true);
    const dataOffset = HEADER_PREFIX_BYTES + headerLength;
    if (dataOffset > buffer.byteLength) throw new Error('The result header is truncated.');

    let header: WinchResultHeader;
    try {
        const json = new TextDecoder('utf-8', { fatal: true }).decode(new Uint8Array(buffer, HEADER_PREFIX_BYTES, headerLength));
        header = JSON.parse(json) as WinchResultHeader;
    } catch (error) {
        throw new Error(`Unable to decode result metadata: ${error instanceof Error ? error.message : String(error)}`);
    }

    if (!header || typeof header !== 'object' || header.schema !== 'aquasim-winch-result' || header.version !== VERSION) {
        throw new Error('The result metadata has an unsupported schema or version.');
    }
    if (!Number.isInteger(header.frameCount) || header.frameCount < 1 ||
        !Number.isInteger(header.frameRecordBytes) || header.frameRecordBytes < FRAME_SUMMARY_BYTES) {
        throw new Error('The result metadata contains an invalid frame layout.');
    }
    if (buffer.byteLength !== dataOffset + header.frameCount * header.frameRecordBytes) {
        throw new Error('The binary result length does not match its declared frame count.');
    }
    if (typeof header.modelName !== 'string' ||
        !Array.isArray(header.nodeIds) || !header.nodeIds.every(Number.isInteger) ||
        new Set(header.nodeIds).size !== header.nodeIds.length ||
        !Array.isArray(header.nodePositionsM) ||
        header.nodePositionsM.length !== header.nodeIds.length ||
        !header.nodePositionsM.every(position =>
            Array.isArray(position) && position.length === 3 && position.every(Number.isFinite)) ||
        !Array.isArray(header.surfaceNodeIds) || !header.surfaceNodeIds.every(Number.isInteger) ||
        !Array.isArray(header.fixedNodeIds) || !header.fixedNodeIds.every(Number.isInteger) ||
        !Array.isArray(header.ropeElements) ||
        !header.ropeElements.every(element =>
            element && Number.isInteger(element.componentId) && Number.isInteger(element.elementId) &&
            typeof element.componentName === 'string') ||
        !Array.isArray(header.winches) || header.winches.length !== 2 ||
        !header.winches.every(winch => winch && typeof winch.name === 'string' &&
            Number.isInteger(winch.componentId) && Array.isArray(winch.topNodeIds) &&
            winch.topNodeIds.every(Number.isInteger))) {
        throw new Error('The result is missing its node IDs, fixed-node constraints, or two winch records.');
    }
    const knownIds = new Set(header.nodeIds);
    if ([...header.fixedNodeIds, ...header.surfaceNodeIds, ...header.winches.flatMap(w => w.topNodeIds)]
        .some(id => !knownIds.has(id)) || new Set(header.fixedNodeIds).size !== header.fixedNodeIds.length ||
        new Set(header.ropeElements.map(e => `${e.componentId}:${e.elementId}`)).size !== header.ropeElements.length) {
        throw new Error('The result contains duplicate or unknown node/element IDs.');
    }
    if (!header.settings || !Number.isFinite(header.settings.takeUpSpeedMPerMin) ||
        !Array.isArray(header.warnings) || !header.warnings.every(value => typeof value === 'string') ||
        !Array.isArray(header.assumptions) || !header.assumptions.every(value => typeof value === 'string')) {
        throw new Error('The result contains invalid settings, warnings, or assumptions.');
    }
    const expectedFrameBytes = FRAME_SUMMARY_BYTES +
        header.nodeIds.length * NODE_POSITION_BYTES +
        header.ropeElements.length * TENSION_BYTES;
    if (header.frameRecordBytes !== expectedFrameBytes) {
        throw new Error('The result metadata contains an invalid dynamic frame layout.');
    }

    const frames: WinchResultFrame[] = [];
    const fields: Array<keyof Omit<WinchResultFrame, 'nodePositionsM' | 'ropeTensionsN'>> = [
        'timeS', 'takeUpM', 'translationZM', 'winch1TensionN', 'winch2TensionN',
        'netVerticalLoadN', 'momentXNm', 'momentYNm', 'momentZNm'
    ];
    const nodeValueCount = header.nodeIds.length * 3;
    const positionBuffer = new Float32Array(nodeValueCount);
    const tensionBuffer = new Float32Array(header.ropeElements.length);
    for (let index = 0; index < header.frameCount; index++) {
        const frameOffset = dataOffset + index * expectedFrameBytes;
        const values = fields.map((_, field) => view.getFloat64(frameOffset + field * 8, true));
        if (!values.every(Number.isFinite)) throw new Error(`Frame ${index} contains a non-finite value.`);
        if (values[0] < 0 || (index > 0 && values[0] <= frames[index - 1].timeS)) {
            throw new Error('Result frame times must be nonnegative and strictly increasing.');
        }
        let cursor = frameOffset + FRAME_SUMMARY_BYTES;
        for (let value = 0; value < nodeValueCount; value++) {
            const nodeValue = view.getFloat32(cursor, true);
            if (!Number.isFinite(nodeValue)) throw new Error(`Frame ${index} contains a non-finite node position.`);
            positionBuffer[value] = nodeValue;
            cursor += TENSION_BYTES;
        }
        const nodePositionsM = positionBuffer.slice();
        for (let value = 0; value < header.ropeElements.length; value++) {
            const tension = view.getFloat32(cursor, true);
            if (!Number.isFinite(tension)) throw new Error(`Frame ${index} contains a non-finite rope tension.`);
            tensionBuffer[value] = tension;
            cursor += TENSION_BYTES;
        }
        frames.push({
            timeS: values[0],
            takeUpM: values[1],
            translationZM: values[2],
            winch1TensionN: values[3],
            winch2TensionN: values[4],
            netVerticalLoadN: values[5],
            momentXNm: values[6],
            momentYNm: values[7],
            momentZNm: values[8],
            nodePositionsM,
            ropeTensionsN: tensionBuffer.slice()
        });
    }

    return { header, frames };
}

export function assertResultMatchesModel(
    result: WinchResult,
    modelName: string,
    nodes: Array<{ id: number; x: number; y: number; z: number }>
): void {
    if (result.header.modelName !== modelName) {
        throw new Error(`This result is for "${result.header.modelName}", not the loaded model "${modelName}".`);
    }
    const expectedNodes = [...nodes].sort((a, b) => a.id - b.id);
    const expectedIds = expectedNodes.map(node => node.id);
    const resultIds = result.header.nodeIds;
    if (resultIds.length !== expectedIds.length || resultIds.some((id, index) => id !== expectedIds[index])) {
        throw new Error('The result node IDs do not match the currently loaded model.');
    }
    const positionsMatch = expectedNodes.every((node, index) => {
        const [x, y, z] = result.header.nodePositionsM[index];
        return Number.isFinite(x) && Number.isFinite(y) && Number.isFinite(z) &&
            Math.abs(node.x - x) <= 1e-6 &&
            Math.abs(node.y - y) <= 1e-6 &&
            Math.abs(node.z - z) <= 1e-6;
    });
    if (!positionsMatch) throw new Error('The result node coordinates do not match the currently loaded model.');
    const surfaceNodeIds = expectedNodes.filter(node => Math.abs(node.z) <= 1e-9).map(node => node.id);
    const recordedSurfaceNodeIds = [...result.header.surfaceNodeIds].sort((a, b) => a - b);
    if (recordedSurfaceNodeIds.length !== surfaceNodeIds.length ||
        recordedSurfaceNodeIds.some((id, index) => id !== surfaceNodeIds[index])) {
        throw new Error('The result surface-node set does not match the loaded model nodes on Z=0.');
    }
    const knownNodeIds = new Set(expectedIds);
    const fixedNodeIds = [...result.header.fixedNodeIds].sort((a, b) => a - b);
    const fixedNodeSet = new Set(fixedNodeIds);
    if (fixedNodeSet.size !== fixedNodeIds.length ||
        fixedNodeIds.some(id => !knownNodeIds.has(id))) {
        throw new Error('The result fixed-node constraints contain duplicates or unknown nodes.');
    }
}
