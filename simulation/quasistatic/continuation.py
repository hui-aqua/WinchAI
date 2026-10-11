"""Time-controlled adaptive cable-length continuation; only equilibria are emitted."""
import math
import numpy as np
from .model import load_model
from .equilibrium import EquilibriumSystem, EquilibriumError, Tolerances


def simulate(model_path, payout_m=1.0, initial_increment_m=0.05, water_density=1025.0,
             speed_m_per_min=1.0, time_step_s=3.0, min_increment_m=1e-4,
             max_steps=100000, include_membrane=True, tolerances=Tolerances()):
    for name,value in [('payout',payout_m),('initial increment',initial_increment_m),
                       ('water density',water_density),('speed',speed_m_per_min),
                       ('time step',time_step_s),('minimum increment',min_increment_m)]:
        if not math.isfinite(value):
            raise ValueError(f'{name} must be finite.')
    if not 0 < min_increment_m <= initial_increment_m <= 0.05:
        raise ValueError('Cable increments must satisfy 0 < minimum <= initial <= 0.05 m.')
    if water_density <= 0 or speed_m_per_min <= 0 or time_step_s <= 0 or max_steps < 1:
        raise ValueError('Density, speed, time step and maximum steps must be positive.')
    if not isinstance(max_steps, int):
        raise ValueError("Maximum steps must be an integer.")
    model = load_model(model_path,water_density,include_membrane)
    if any(e['length']+payout_m*e['fraction'] <= 1e-6 for e in model.edges):
        raise ValueError('Retrieval exceeds available unstretched cable length.')
    system = EquilibriumSystem(model,tolerances)
    frames = {key: [] for key in ('timeS','takeUpM','translationZM','winchTensionsN',
                                  'netVerticalLoadN','nodePositionsM','ropeTensionsN','momentsNm')}
    convergence = []
    out_indices = [i for i,e in enumerate(model.edges) if e['output']]
    winch_indices = []
    for winch in model.metadata['winches']:
        top = set(winch['topNodeIds'])
        winch_indices.append([i for i,e in enumerate(model.edges)
                              if e.get('componentId') == winch['componentId'] and
                              (model.metadata['nodeIds'][e['a']] in top or
                               model.metadata['nodeIds'][e['b']] in top)])

    def record(payout, state, metric):
        # Retain only playback data, not residuals/active sets for every step.
        frames['timeS'].append(abs(payout)*60/speed_m_per_min)
        frames['takeUpM'].append(-payout)
        frames['translationZM'].append(float(np.mean(state['positions'][:,2]-model.positions[:,2])))
        frames['winchTensionsN'].append([float(state['tensions'][indices].sum()) for indices in winch_indices])
        frames['netVerticalLoadN'].append(state['netVerticalLoad'])
        frames['nodePositionsM'].append(state['positions'].astype(np.float32))
        frames['ropeTensionsN'].append(state['tensions'][out_indices].astype(np.float32))
        frames['momentsNm'].append(state['moment'])
        convergence.append(metric)

    x, state, metric = system.solve(0.0)
    metric.update(payoutM=0.0, incrementM=0.0, event=False)
    record(0.0,state,metric)
    limit = min(initial_increment_m,speed_m_per_min*time_step_s/60)
    step, current, attempts, retries, events = limit,0.0,0,0,0
    direction = 1 if payout_m >= 0 else -1
    while abs(payout_m-current) > 1e-12:
        if attempts >= max_steps:
            raise EquilibriumError('Adaptive continuation exceeded maximum trial steps; no result was written.')
        trial_step = min(step,abs(payout_m-current))
        target = current+direction*trial_step
        attempts += 1
        try:
            candidate_x,candidate,candidate_metric = system.solve(target,x)
            crossing = np.any((state['positions'][:,2] < 0) != (candidate['positions'][:,2] < 0))
            active_change = np.any(state['active'] != candidate['active'])
            # Circular-section immersion changes continuously before axis crossing.
            surface_change = abs(candidate['buoyancy']-state['buoyancy']) > max(10.0,0.01*abs(state['buoyancy']))
            event = bool(crossing or active_change or surface_change)
            if event and trial_step > min_increment_m*(1+1e-9):
                step = max(min_increment_m,trial_step/2)
                retries += 1
                continue
        except EquilibriumError:
            if trial_step <= min_increment_m*(1+1e-9):
                raise
            step = max(min_increment_m,trial_step/2)
            retries += 1
            continue
        current,x,state = target,candidate_x,candidate
        candidate_metric['payoutM'] = current
        candidate_metric['incrementM'] = trial_step
        candidate_metric['event'] = event
        record(current,state,candidate_metric)
        events += int(event)
        step = min(limit,trial_step*(1.25 if event else 1.5))
    frames = {key:np.asarray(value) for key,value in frames.items()}
    return model.metadata,dict(frames=frames,
        ropeElements=[{k:model.edges[i][k] for k in ('componentId','componentName','elementId')} for i in out_indices],
        warnings=model.metadata['warnings'],convergence=convergence,
        settings=dict(solverMethod='quasi-static',payoutM=payout_m,initialIncrementM=initial_increment_m,
                      minimumIncrementM=min_increment_m,timeStepSeconds=time_step_s,
                      takeUpSpeedMPerMin=-direction*speed_m_per_min,durationSeconds=abs(payout_m)*60/speed_m_per_min,
                      frameIntervalSeconds=0.0,integrationStepSeconds=0.0,internalSteps=attempts,
                      acceptedSteps=len(convergence)-1,rejectedSteps=retries,eventSteps=events,
                      membraneDynamics=include_membrane,hydrodynamicDrag=False,addedMass=False,
                      tolerances=vars(tolerances)))
