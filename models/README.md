# Local models

Place private `.amodel` files here. Model files in this folder are ignored by
Git, denied by the development server, and excluded from production assets.
Load them through the browser file picker or pass their paths to the Python CLI.
The optional target-model integration tests use `Next_Otervika_NLS_AI.amodel`;
they skip when that local file is unavailable.
