/** Locate bracketing states without scanning every adaptive step on each tick. */
export function findPlaybackInterval(frames: ReadonlyArray<{ timeS: number }>, timeS: number): {
    lower: number; upper: number; fraction: number;
} {
    if (frames.length === 0) throw new Error('Playback requires at least one frame.');
    const time = Math.max(frames[0].timeS, Math.min(frames[frames.length - 1].timeS, timeS));
    let left = 0;
    let right = frames.length - 1;
    while (left < right) {
        const middle = Math.floor((left + right) / 2);
        if (frames[middle].timeS < time) left = middle + 1;
        else right = middle;
    }
    const upper = left;
    const lower = Math.max(0, upper - 1);
    const interval = frames[upper].timeS - frames[lower].timeS;
    return { lower, upper, fraction: interval > 0 ? (time - frames[lower].timeS) / interval : 0 };
}

/** Adaptive step spacing must be represented by elapsed time on force plots. */
export function playbackTimeFraction(frames: ReadonlyArray<{ timeS: number }>, timeS: number): number {
    const start = frames[0]?.timeS ?? 0;
    const duration = (frames[frames.length - 1]?.timeS ?? start) - start;
    return duration > 0 ? Math.max(0, Math.min(1, (timeS - start) / duration)) : 0;
}
