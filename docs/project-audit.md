# Project structure audit

The browser and Python solver remain separate applications connected by AQWR
results and the loopback API. The structure follows these responsibilities:

| Location | Responsibility |
| --- | --- |
| `src/parser/` | Browser XML parsing and normalized display types |
| `src/viewer/` | Three.js scene, camera, geometry and resource ownership |
| `src/ui/` | Controls, model loading and simulation playback |
| `src/simulation/` | Binary result validation and playback time calculations |
| `simulation/model_io.py` | Python XML validation and SI material conversion |
| `simulation/quasistatic/` | Model normalization, hydrostatics, equilibrium and continuation |
| `simulation/result_format.py` | AQWR binary encoding |
| `simulation/winch_solver.py` | CLI arguments and solver orchestration |
| `simulation/local_server.py` | Loopback HTTP service |
| `tests/`, `tests_python/` | Frontend and Python regression tests |
| `models/` | Private local inputs, excluded from Git and web serving |
| `public/branding/` | Public runtime images |
| `docs/` | User guide, solver assumptions and project maintenance notes |
| `scripts/` | Local application launcher |

## Removed or relocated

- Removed three retired XPBD/Numba benchmarks in `scratch/`; they imported
  deleted dynamics modules and could no longer run.
- Moved the private source model from `public/` into `models/`, verifying that
  its bytes were unchanged. This removes the need to copy it into a web build
  and delete it afterward. The development server denies the entire folder.
- Moved binary encoding out of the CLI into `simulation/result_format.py`.
- Renamed the input regression suite to `test_model_io.py` and updated imports.
- Added ignore rules for local model files and Python environments.

Dependencies, production builds and saved simulation results are retained.
They are runtime resources or user artifacts, rather than obsolete source.

## Corrected behavior

- Support propagation follows connected beam edges. Disconnected sections of
  one named component no longer inherit unrelated fixed supports.
- Duplicate IDs and invalid line/net node references report explicit input
  errors. Model normalization parses XML once instead of twice.
- Excess retrieval is rejected before solving intermediate states.
- Continuation retains compact playback snapshots and convergence metrics;
  full residuals and active sets are retained only for the current state.
- Adaptive playback uses binary search, and force plots use elapsed time
  rather than frame index. Plot scaling avoids large spread argument lists.
- Malformed result metadata, duplicate IDs, and nonmonotonic times are rejected.
- Duplicate form submissions are ignored. Results from a model replaced during
  a pending simulation are discarded.
- Python regressions now run alongside frontend checks in deployment and release
  CI. Optional local-model tests skip when the private input is unavailable.

## Validation limits

Unit and integration regressions cover analytical mechanical cases and local
model metadata. They do not establish full-travel convergence for the large
source model. The solver still uses rigid beams, axial net approximations and
explicit cable connectivity; contact, friction and automatic fairlead routing
remain outside the implemented model. See `quasi-static-design.md` for details.
