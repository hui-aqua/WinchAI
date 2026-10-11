import { existsSync } from 'node:fs';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const localPython = process.platform === 'win32'
    ? path.join(projectRoot, '.venv', 'Scripts', 'python.exe')
    : path.join(projectRoot, '.venv', 'bin', 'python');
const python = process.env.WINCHAI_PYTHON || (existsSync(localPython) ? localPython : process.platform === 'win32' ? 'python' : 'python3');
const dependencyCheck = spawnSync(python, ['-c', 'import numpy; from scipy.optimize import least_squares'], {
    cwd: projectRoot,
    encoding: 'utf8'
});
if (dependencyCheck.error || dependencyCheck.status !== 0) {
    console.error(`Simulation dependencies are unavailable in "${python}".`);
    console.error(dependencyCheck.error?.message || dependencyCheck.stderr?.trim());
    console.error(`Install them with: "${python}" -m pip install -r requirements.txt`);
    process.exit(1);
}
const server = spawn(python, ['-m', 'simulation.local_server'], {
    cwd: projectRoot,
    stdio: 'inherit'
});

server.on('error', (error) => {
    console.error(`Could not start the local Python server with "${python}": ${error.message}`);
    process.exitCode = 1;
});
server.on('exit', (code, signal) => {
    if (signal) process.kill(process.pid, signal);
    else process.exitCode = code ?? 1;
});
