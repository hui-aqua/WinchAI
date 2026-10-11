import type { setupSimulationPlayback } from '@/ui/simulationPlayback';

type SimulationPlayback = ReturnType<typeof setupSimulationPlayback>;

interface SimulationError {
    error?: string;
}

export function setupWinchSimulation(playback: SimulationPlayback): {
    setModelFile: (file: File | null) => void;
} {
    const runButton = document.getElementById('btn-run-winch-simulation') as HTMLButtonElement;
    const dialog = document.getElementById('winch-simulation-dialog') as HTMLDialogElement;
    const form = document.getElementById('winch-simulation-form') as HTMLFormElement;
    const modelName = document.getElementById('winch-model-name') as HTMLElement;
    const payout = document.getElementById('winch-payout') as HTMLInputElement;
    const increment = document.getElementById('winch-increment') as HTMLInputElement;
    const status = document.getElementById('winch-run-status') as HTMLElement;
    const submitButton = document.getElementById('btn-submit-winch-simulation') as HTMLButtonElement;
    const cancelButton = document.getElementById('btn-cancel-winch-simulation') as HTMLButtonElement;
    let modelFile: File | null = null;
    let running = false;
    let modelRevision = 0;

    runButton.addEventListener('click', () => {
        if (!modelFile) return;
        modelName.textContent = `Model: ${modelFile.name}`;
        status.textContent = '';
        dialog.showModal();
    });

    cancelButton.addEventListener('click', () => dialog.close());

    form.addEventListener('submit', (event) => {
        event.preventDefault();
        if (running || !modelFile || !form.reportValidity()) return;
        void runSimulation(modelFile);
    });

    async function runSimulation(file: File): Promise<void> {
        running = true;
        const revision = modelRevision;
        const params = new URLSearchParams({
            payout_m: payout.value,
            initial_increment_m: String(Number(increment.value) / 1000)
        });

        submitButton.disabled = true;
        cancelButton.disabled = true;
        status.textContent = 'Solving quasi-static equilibrium steps locally. This can take some time; keep this window open.';
        try {
            const response = await fetch(`/api/simulate?${params}`, {
                method: 'POST',
                headers: { 'X-WinchAI-Request': '1' },
                body: file
            });
            if (!response.ok) {
                const message = await response.text();
                let detail = message;
                try {
                    detail = (JSON.parse(message) as SimulationError).error || message;
                } catch {
                    // Keep the response body when the local server does not return JSON.
                }
                throw new Error(detail || `Local simulation failed (HTTP ${response.status}).`);
            }
            const buffer = await response.arrayBuffer();
            if (revision !== modelRevision) {
                status.textContent = 'The loaded model changed during simulation. Run again using the current model.';
                return;
            }
            playback.loadResult(buffer);
            status.textContent = 'All requested equilibrium steps converged. The result is loaded in the playback panel.';
            dialog.close();
        } catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            status.textContent = `Simulation failed: ${message}`;
        } finally {
            running = false;
            submitButton.disabled = false;
            cancelButton.disabled = false;
        }
    }

    return {
        setModelFile(file: File | null): void {
            modelRevision++;
            modelFile = file;
            runButton.disabled = file === null;
            runButton.title = file
                ? `Run the local Python winch solver using ${file.name}`
                : 'Load a model before running the winch simulation';
        }
    };
}
