import { describe, expect, it } from 'vitest';
import { findPlaybackInterval, playbackTimeFraction } from '@/simulation/playbackMath';

describe('adaptive equilibrium playback', () => {
    const frames = [0, 3, 3.01, 6].map(timeS => ({ timeS }));

    it('interpolates over elapsed time despite very uneven step spacing', () => {
        const interval = findPlaybackInterval(frames, 3.005);
        expect(interval.lower).toBe(1);
        expect(interval.upper).toBe(2);
        expect(interval.fraction).toBeCloseTo(0.5);
        expect(playbackTimeFraction(frames, 3.01)).toBeCloseTo(3.01 / 6);
    });

    it('handles a single equilibrium and clamps time to the available states', () => {
        expect(findPlaybackInterval([{ timeS: 0 }], 10)).toEqual({ lower: 0, upper: 0, fraction: 0 });
        expect(findPlaybackInterval(frames, -1)).toEqual({ lower: 0, upper: 0, fraction: 0 });
        expect(findPlaybackInterval(frames, 100)).toEqual({ lower: 2, upper: 3, fraction: 1 });
        expect(playbackTimeFraction([{ timeS: 0 }], 10)).toBe(0);
    });
});
