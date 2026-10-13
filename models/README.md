# Local models

Keep private AquaSim `.amodel` inputs and timestamped `.bak` source snapshots here.
Most model files are ignored by Git, denied by the development server, and excluded
from production assets. `Next_Otervika_NLS3.amodel` is the tracked project example.
Load models through the browser file picker or pass their paths to the Python CLI.
The optional target-model integration tests use `Next_Otervika_NLS_AI.amodel` and
skip when that local file is unavailable.