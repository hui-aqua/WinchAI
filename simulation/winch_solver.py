#!/usr/bin/env python3
"""Solve three-dimensional quasi-static winch equilibria for AquaSim models."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

from .result_format import encode_result


def run(model_path: Path, output_path: Path, water_density: float = 1025.0,
        payout_m: float = 1.0, initial_increment_m: float = 0.05,
        speed_m_per_min: float = 1.0, time_step_s: float = 3.0,
        min_increment_m: float = 1e-4, max_steps: int = 100000,
        include_membrane: bool = True, tolerances=None) -> None:
    from .quasistatic import simulate, Tolerances
    metadata, equilibrium = simulate(
        model_path, payout_m, initial_increment_m, water_density,
        speed_m_per_min, time_step_s, min_increment_m, max_steps,
        include_membrane, tolerances or Tolerances())
    result = encode_result(metadata, equilibrium)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(result)
    settings = equilibrium['settings']
    print(f"Wrote {len(result):,} bytes to {output_path}")
    print(f"Quasi-static: {settings['acceptedSteps']} accepted steps, "
          f"{settings['rejectedSteps']} retries; payout {payout_m:g} m.")
    for warning in equilibrium['warnings']:
        print(f"WARNING: {warning}", file=sys.stderr)


def main() -> int:
    from .quasistatic import Tolerances
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('model', type=Path, help='Local AquaSim .amodel file.')
    parser.add_argument('--output', type=Path, default=Path('simulation_results/winch-result.aqwr'))
    parser.add_argument('--water-density-kg-m3', type=float, default=1025.0)
    parser.add_argument('--payout-m', type=float, default=1.0, help='Positive lowers; negative retrieves cable.')
    parser.add_argument('--initial-increment-mm', type=float, default=50.0)
    parser.add_argument('--minimum-increment-mm', type=float, default=0.1)
    parser.add_argument('--speed-m-per-min', type=float, default=1.0)
    parser.add_argument('--time-step-seconds', type=float, default=3.0)
    parser.add_argument('--max-steps', type=int, default=100000)
    parser.add_argument('--max-evaluations', type=int, default=300)
    parser.add_argument('--force-tolerance-n', type=float, default=1.0)
    parser.add_argument('--moment-tolerance-nm', type=float, default=1.0)
    parser.add_argument('--displacement-tolerance-m', type=float, default=1e-5)
    parser.add_argument('--tension-tolerance-n', type=float, default=1.0)
    parser.add_argument('--buoyancy-tolerance-n', type=float, default=1.0)
    parser.add_argument('--constraint-tolerance-m', type=float, default=1e-8)
    parser.add_argument('--no-membrane', action='store_true')
    args = parser.parse_args()
    try:
        run(args.model,args.output,args.water_density_kg_m3,args.payout_m,
            args.initial_increment_mm/1000,args.speed_m_per_min,args.time_step_seconds,
            args.minimum_increment_mm/1000,args.max_steps,not args.no_membrane,
            Tolerances(args.force_tolerance_n,args.moment_tolerance_nm,
                       args.displacement_tolerance_m,args.tension_tolerance_n,
                       args.buoyancy_tolerance_n,args.constraint_tolerance_m,args.max_evaluations))
    except (ValueError,OSError,RuntimeError) as error:
        parser.error(str(error))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
