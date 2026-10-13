import { describe, expect, it } from 'vitest';
import { assertResultMatchesModel, parseWinchResult } from '@/simulation/resultFormat';

function makeResultBuffer(modify?: (header: Record<string, unknown>) => void, times = [0]): ArrayBuffer {
    const header = {
        schema: 'aquasim-winch-result',
        version: 2,
        modelName: 'physics-test',
        nodeIds: [1, 2],
        nodePositionsM: [[0, 0, 0], [1, 2, 3]],
        surfaceNodeIds: [1],
        fixedNodeIds: [1],
        ropeElements: [
            { componentId: 1, componentName: 'winch1', elementId: 10 },
            { componentId: 2, componentName: 'winch2', elementId: 20 }
        ],
        frameCount: times.length,
        frameRecordBytes: 104,
        settings: {
            takeUpSpeedMPerMin: 1,
            durationSeconds: 1,
            frameIntervalSeconds: 60,
            integrationStepSeconds: 0.001,
            internalSteps: 1000,
            waterDensityKgM3: 1025,
            membraneDynamics: false,
            hydrodynamicDrag: false,
            addedMass: false
        },
        beamLoads: [],
        winches: [
            { name: 'winch1', componentId: 1, topNodeIds: [1] },
            { name: 'winch2', componentId: 2, topNodeIds: [2] }
        ],
        centerOfMassM: [0, 0, 0],
        totalMassKg: 1,
        weightN: 10,
        buoyancyN: 2,
        netVerticalLoadN: 8,
        momentNm: [0, 0, 0],
        warnings: [],
        assumptions: []
    };
    modify?.(header);
    const metadata = new TextEncoder().encode(JSON.stringify(header));
    const buffer = new ArrayBuffer(10 + metadata.length + header.frameRecordBytes * times.length);
    const bytes = new Uint8Array(buffer);
    bytes.set([65, 81, 87, 82]);
    const view = new DataView(buffer);
    view.setUint16(4, 2, true);
    view.setUint32(6, metadata.length, true);
    bytes.set(metadata, 10);
    times.forEach((time, frameIndex) => {
        const recordOffset = 10 + metadata.length + frameIndex * header.frameRecordBytes;
        [time, 0, 0, 4, 4, 8, 0, 0, 0].forEach((value, index) => {
            view.setFloat64(recordOffset + index * 8, value, true);
        });
        const frameOffset = recordOffset + 72;
        [0, 0, 0, 1, 2, 3].forEach((value, index) => {
            view.setFloat32(frameOffset + index * 4, value, true);
        });
        view.setFloat32(frameOffset + 24, 4, true);
        view.setFloat32(frameOffset + 28, 5, true);
    });
    return buffer;
}

describe('binary winch simulation results', () => {
    it('decodes frame values and validates that results match the loaded model', () => {
        const result = parseWinchResult(makeResultBuffer());
        expect(result.frames[0]).toEqual({
            timeS: 0,
            takeUpM: 0,
            translationZM: 0,
            winch1TensionN: 4,
            winch2TensionN: 4,
            netVerticalLoadN: 8,
            momentXNm: 0,
            momentYNm: 0,
            momentZNm: 0,
            nodePositionsM: new Float32Array([0, 0, 0, 1, 2, 3]),
            ropeTensionsN: new Float32Array([4, 5])
        });
        expect(() => assertResultMatchesModel(result, 'physics-test', [
            { id: 2, x: 1, y: 2, z: 3 },
            { id: 1, x: 0, y: 0, z: 0 }
        ])).not.toThrow();
    });

    it('rejects the wrong model and truncated frame data', () => {
        const buffer = makeResultBuffer();
        const result = parseWinchResult(buffer);
        expect(() => assertResultMatchesModel(result, 'another-model', [
            { id: 1, x: 0, y: 0, z: 0 },
            { id: 2, x: 1, y: 2, z: 3 }
        ])).toThrow(/not the loaded model/);
        expect(() => parseWinchResult(buffer.slice(0, buffer.byteLength - 1))).toThrow(/length/);
    });
    it('rejects ambiguous IDs and malformed playback metadata', () => {
        expect(() => parseWinchResult(makeResultBuffer(h => { h.nodeIds = [1, 1]; }))).toThrow();
        expect(() => parseWinchResult(makeResultBuffer(h => { h.fixedNodeIds = [999]; }))).toThrow(/unknown/);
        expect(() => parseWinchResult(makeResultBuffer(h => { h.ropeElements = [null, null]; }))).toThrow(/records/);
        expect(() => parseWinchResult(makeResultBuffer(h => { h.warnings = null; }))).toThrow(/warnings/);
        expect(() => parseWinchResult(makeResultBuffer(h => { h.settings = {}; }))).toThrow(/settings/);
    });

    it('rejects repeated, reversed, and negative frame times', () => {
        for (const times of [[0, 0], [1, 0], [-1]]) {
            expect(() => parseWinchResult(makeResultBuffer(undefined, times))).toThrow(/times/);
        }
        expect(parseWinchResult(makeResultBuffer(undefined, [0, 0.001, 3])).frames).toHaveLength(3);
    });

});
