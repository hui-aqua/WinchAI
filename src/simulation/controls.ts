export interface ComponentProperties {
    componentId: number;
    name: string;
    type: 'beam' | 'truss' | 'membrane';
    eaN: number | null;
    lengthM: number;
    massKg: number;
    weightInAirN: number;
    weightInWaterN: number;
    pathLengthsM?: number[];
}

export interface WinchProperties {
    componentId: number;
    name: string;
    pathLengthsM: number[];
}

export interface ModelProperties {
    components: ComponentProperties[];
    winches: WinchProperties[];
}

export interface SimulationControls {
    components: Array<{
        componentId: number;
        eaN?: number;
        lengthM?: number;
        massKg?: number;
        weightInWaterN?: number;
    }>;
    winches: Array<{ componentId: number; payoutM: number }>;
    durationSeconds: number;
    saveIntervalSeconds: number;
    initialIncrementM: number;
}
