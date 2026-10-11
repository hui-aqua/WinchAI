"""Constant-rate winch schedules, adaptive equilibrium steps and timed output."""
import math
import numpy as np
from .model import load_model
from .properties import apply_component_overrides
from .equilibrium import EquilibriumSystem, EquilibriumError, Tolerances


def simulate(model_path, payout_m=1.0, initial_increment_m=0.05, water_density=1025.0,
             speed_m_per_min=1.0, time_step_s=3.0, min_increment_m=1e-4,
             max_steps=100000, include_membrane=True, tolerances=Tolerances(), *,
             component_overrides=None, winch_payouts=None, duration_seconds=None,
             save_interval_seconds=None, max_result_bytes=None):
    for name,value in [('payout',payout_m),('initial increment',initial_increment_m),
                       ('water density',water_density),('speed',speed_m_per_min),
                       ('time step',time_step_s),('minimum increment',min_increment_m)]:
        if not math.isfinite(value):
            raise ValueError(f'{name} must be finite.')
    if not 0 < min_increment_m <= initial_increment_m <= 0.05:
        raise ValueError('Cable increments must satisfy 0 < minimum <= initial <= 0.05 m.')
    if water_density <= 0 or speed_m_per_min <= 0 or time_step_s <= 0 or max_steps < 1:
        raise ValueError('Density, speed, time step and maximum steps must be positive.')
    if not isinstance(max_steps,int) or isinstance(max_steps,bool):
        raise ValueError('Maximum steps must be an integer.')
    for name,value in [('duration',duration_seconds),('save interval',save_interval_seconds)]:
        if value is not None and (not math.isfinite(value) or value <= 0):
            raise ValueError(f'{name} must be finite and positive.')
    if max_result_bytes is not None and (not isinstance(max_result_bytes,int) or max_result_bytes <= 0):
        raise ValueError('Result byte limit must be a positive integer.')
    model = load_model(model_path,water_density,include_membrane)
    apply_component_overrides(model,component_overrides)
    winch_ids = [w['componentId'] for w in model.metadata['winches']]
    if winch_payouts is None:
        payouts = np.full(len(winch_ids),payout_m,dtype=float)
    else:
        if not isinstance(winch_payouts,dict) or any(cid not in winch_ids for cid in winch_payouts):
            raise ValueError('Unknown winch component ID.')
        values = [winch_payouts.get(cid,0.0) for cid in winch_ids]
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in values):
            raise ValueError('Winch payouts must be finite numbers.')
        payouts = np.asarray(values,dtype=float)
    travel = float(np.max(np.abs(payouts),initial=0))
    duration = duration_seconds if duration_seconds is not None else travel*60/speed_m_per_min
    system = EquilibriumSystem(model,tolerances)
    # Reject invalid cable lengths before starting any equilibrium solves.
    system.evaluate(system.x0,payouts)
    frames = {key: [] for key in ('timeS','takeUpM','translationZM','winchTensionsN',
                                  'netVerticalLoadN','nodePositionsM','ropeTensionsN','momentsNm')}
    convergence = []
    out_indices = [i for i,e in enumerate(model.edges) if e['output']]
    frame_bytes = 72+len(model.positions)*12+len(out_indices)*4
    if save_interval_seconds is not None and max_result_bytes is not None:
        frame_intervals = duration/save_interval_seconds
        if not math.isfinite(frame_intervals) or frame_intervals*frame_bytes > max_result_bytes or (math.ceil(frame_intervals)+1)*frame_bytes > max_result_bytes:
            raise ValueError('Requested saved frames exceed the result limit. Increase save ΔT.')
    winch_indices = []
    for winch in model.metadata['winches']:
        top = set(winch['topNodeIds'])
        winch_indices.append([i for i,e in enumerate(model.edges)
                              if e.get('componentId') == winch['componentId'] and e['output'] and
                              (model.metadata['nodeIds'][e['a']] in top or
                               model.metadata['nodeIds'][e['b']] in top)])

    def record(time,state,metric):
        if max_result_bytes is not None and (len(convergence)+1)*frame_bytes > max_result_bytes:
            raise ValueError('Saved frames exceed the result limit. Increase save ΔT.')
        progress = time/duration if duration > 0 else 0.0
        metric = dict(metric,timeS=time,payoutM=float(np.mean(payouts)*progress),
                      winchPayoutsM=(payouts*progress).tolist())
        frames['timeS'].append(time)
        frames['takeUpM'].append(-float(np.mean(payouts)*progress))
        frames['translationZM'].append(float(np.mean(state['positions'][:,2]-model.positions[:,2])))
        frames['winchTensionsN'].append([float(state['tensions'][indices].sum()) for indices in winch_indices])
        frames['netVerticalLoadN'].append(state['netVerticalLoad'])
        frames['nodePositionsM'].append(state['positions'].astype(np.float32))
        frames['ropeTensionsN'].append(state['tensions'][out_indices].astype(np.float32))
        frames['momentsNm'].append(state['moment'])
        convergence.append(metric)

    x,state,metric = system.solve(np.zeros(len(winch_ids)))
    metric.update(incrementM=0.0,event=False)
    record(0.0,state,metric)
    # Cable increments cap the fastest winch; all winches share elapsed time.
    speed = travel/duration if duration > 0 else 0.0
    limit = min(time_step_s,initial_increment_m/speed) if speed > 0 else time_step_s
    minimum_step = min_increment_m/speed if speed > 0 else limit
    step,current,attempts,retries,events,accepted = limit,0.0,0,0,0,0
    save_number = 1
    while current < duration:
        if attempts >= max_steps:
            raise EquilibriumError('Adaptive continuation exceeded maximum trial steps; no result was written.')
        deadline = min(duration,save_number*save_interval_seconds) if save_interval_seconds is not None else duration
        # Constant-length runs can reuse a validated equilibrium at save times.
        target = deadline if travel == 0 else min(current+step,deadline,duration)
        if target <= current:
            raise ValueError('The requested time increment is too small to advance simulation time.')
        trial_step = target-current
        attempts += 1
        try:
            if travel == 0:
                candidate_x,candidate,candidate_metric = x,state,dict(metric)
            else:
                candidate_x,candidate,candidate_metric = system.solve(payouts*(target/duration),x)
            crossing = np.any((state['positions'][:,2] < 0) != (candidate['positions'][:,2] < 0))
            active_change = np.any(state['active'] != candidate['active'])
            surface_change = abs(candidate['buoyancy']-state['buoyancy']) > max(10.0,0.01*abs(state['buoyancy']))
            event = bool(crossing or active_change or surface_change)
            if event and trial_step > minimum_step*(1+1e-9):
                step = max(minimum_step,trial_step/2)
                retries += 1
                continue
        except EquilibriumError:
            if trial_step <= minimum_step*(1+1e-9):
                raise
            step = max(minimum_step,trial_step/2)
            retries += 1
            continue
        current,x,state,metric = target,candidate_x,candidate,candidate_metric
        metric = dict(metric,incrementM=trial_step*speed,event=event)
        accepted += 1
        if save_interval_seconds is None or current == deadline:
            record(current,state,metric)
            if save_interval_seconds is not None:
                save_number += 1
        events += int(event)
        step = min(limit,trial_step*(1.25 if event else 1.5))
    frames = {key:np.asarray(value) for key,value in frames.items()}
    return model.metadata,dict(frames=frames,
        ropeElements=[{k:model.edges[i][k] for k in ('componentId','componentName','elementId')} for i in out_indices],
        warnings=model.metadata['warnings'],convergence=convergence,
        settings=dict(solverMethod='quasi-static',payoutM=float(np.mean(payouts)),
                      winchPayoutsM={str(cid):float(payouts[i]) for i,cid in enumerate(winch_ids)},
                      initialIncrementM=initial_increment_m,minimumIncrementM=min_increment_m,
                      timeStepSeconds=time_step_s,takeUpSpeedMPerMin=-float(np.mean(payouts))*60/duration if duration > 0 else 0,
                      durationSeconds=duration,frameIntervalSeconds=save_interval_seconds or 0,
                      integrationStepSeconds=0.0,internalSteps=attempts,acceptedSteps=accepted,
                      rejectedSteps=retries,eventSteps=events,membraneDynamics=include_membrane,
                      hydrodynamicDrag=False,addedMass=False,tolerances=vars(tolerances)))
