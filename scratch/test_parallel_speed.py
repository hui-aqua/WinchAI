import time
import math
import numpy as np
from numba import njit, prange
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

from simulation.winch_solver import _read_model
from simulation.rope_dynamics import simulate_model

print("Testing baseline simulate_model (sequential)...")
t0 = time.perf_counter()
res_seq = simulate_model("public/Next_Otervika_NLS_AI.amodel", 1025.0, 5.0, 1.0, 500000, solver_method="xpbd", step_seconds=0.05, iterations=2)
t1 = time.perf_counter()
print(f"Sequential 5s simulation took {t1 - t0:.3f} s")
