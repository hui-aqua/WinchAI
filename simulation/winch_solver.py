#!/usr/bin/env python3
"""Solve three-dimensional quasi-static winch equilibria for AquaSim models."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

from .result_format import encode_result


def run(model_path: Path, output_path: Path, water_density: float = 1025.0,
        payout_m: float = 1.0, initial_increment_m: float = 0.05,
        speed_m_per_min: float = 1.0, time_step_s: float = 3.0,
        min_increment_m: float = 1e-4, max_steps: int = 100000,
        include_membrane: bool = True, tolerances=None, *, controls=None,
        duration_seconds=None, save_interval_seconds=None, max_result_bytes=None,
        workers=None, velocity_profile=None, solver_strategy='parallel-rope-length') -> None:
    from .quasistatic import simulate, Tolerances
    options = dict(initial_increment_m=initial_increment_m,duration_seconds=duration_seconds,
                   save_interval_seconds=save_interval_seconds,workers=workers,velocity_profile=velocity_profile,solver_strategy=solver_strategy)
    if controls is not None:
        from .controls import parse_controls
        options.update({key:value for key,value in parse_controls(controls).items() if value is not None})
    metadata, equilibrium = simulate(
        model_path,payout_m=payout_m,water_density=water_density,
        speed_m_per_min=speed_m_per_min,time_step_s=time_step_s,
        min_increment_m=min_increment_m,max_steps=max_steps,
        include_membrane=include_membrane,tolerances=tolerances or Tolerances(),
        max_result_bytes=max_result_bytes,**options)
    result = encode_result(metadata, equilibrium)
    if max_result_bytes is not None and len(result) > max_result_bytes:
        raise ValueError('Result exceeds the byte limit. Increase save ΔT.')
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(result)
    settings = equilibrium['settings']
    print(f"Wrote {len(result):,} bytes to {output_path}")
    print(f"Quasi-static ({settings['equilibriumStrategy']}): {settings['acceptedSteps']} accepted steps, "
          f"{settings['rejectedSteps']} retries; winch payouts {settings['winchPayoutsM']} m.")
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
    parser.add_argument('--solver', choices=('time-marching','parallel-rope-length'),
                        default='parallel-rope-length', help='Sequential implicit equilibrium marching or parallel rope-length equilibria.')
    parser.add_argument('--parallel-workers', type=int, help='CPU workers for independent rope-length states.')
    parser.add_argument('--max-steps', type=int, default=100000)
    parser.add_argument('--max-evaluations', type=int, default=300)
    parser.add_argument('--force-tolerance-n', type=float, default=1.0)
    parser.add_argument('--moment-tolerance-nm', type=float, default=1.0)
    parser.add_argument('--displacement-tolerance-m', type=float, default=1e-5)
    parser.add_argument('--tension-tolerance-n', type=float, default=1.0)
    parser.add_argument('--buoyancy-tolerance-n', type=float, default=1.0)
    parser.add_argument('--constraint-tolerance-m', type=float, default=1e-8)
    parser.add_argument('--settings-file', type=Path, help='JSON component properties, winch payouts, duration and save interval.')
    parser.add_argument('--duration-seconds', type=float)
    parser.add_argument('--save-interval-seconds', type=float)
    parser.add_argument('--max-result-bytes', type=int)
    parser.add_argument('--no-membrane', action='store_true')
    args = parser.parse_args()
    try:
        controls = json.loads(args.settings_file.read_text(encoding='utf-8')) if args.settings_file else None
        run(args.model,args.output,args.water_density_kg_m3,args.payout_m,
            args.initial_increment_mm/1000,args.speed_m_per_min,args.time_step_seconds,
            args.minimum_increment_mm/1000,args.max_steps,not args.no_membrane,
            Tolerances(args.force_tolerance_n,args.moment_tolerance_nm,
                       args.displacement_tolerance_m,args.tension_tolerance_n,
                       args.buoyancy_tolerance_n,args.constraint_tolerance_m,args.max_evaluations),
            controls=controls,duration_seconds=args.duration_seconds,
            save_interval_seconds=args.save_interval_seconds,max_result_bytes=args.max_result_bytes,workers=args.parallel_workers,solver_strategy=args.solver)
    except (ValueError,OSError,RuntimeError) as error:
        parser.error(str(error))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
