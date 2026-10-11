"""Sequential implicit static solves with adaptive time steps.

Each new time solves nonlinear equilibrium using the last accepted pose as
its initial guess. This is the original quasi-static marching strategy;
there are no inertia terms or dynamic integration.
"""
import numpy as np
from .equilibrium import EquilibriumError


class TimeMarchingSolver:
    def __init__(self, system, payouts, schedule, increment, minimum, time_step, max_steps):
        self.system, self.payouts, self.schedule = system, payouts, schedule
        self.travel = float(np.max(np.abs(payouts), initial=0))
        speed = (float(np.max(np.abs(schedule.velocities))) if schedule.profile is not None
                 else self.travel/schedule.duration if schedule.duration else 0.0)
        self.limit = min(time_step, increment/speed) if speed else time_step
        self.minimum = minimum/speed if speed else self.limit
        self.max_steps = max_steps
        self.attempts = self.accepted = self.retries = self.events = 0

    def frames(self, save_interval):
        schedule = self.schedule
        x, state, metric = self.system.solve(self.payouts*schedule.at(0))
        yield 0.0, state, dict(metric, incrementM=0.0, event=False)
        current, step, save_number = 0.0, self.limit, 1
        while current < schedule.duration:
            if self.attempts >= self.max_steps:
                raise EquilibriumError('Implicit time marching exceeded maximum trial steps; no result was written.')
            deadline = min(schedule.duration, save_number*save_interval) if save_interval else schedule.duration
            target = min(current+step, deadline)
            if schedule.profile is not None:
                following = schedule.times[schedule.times > current]
                if len(following):
                    target = min(target, float(following[0]))
            if target <= current:
                raise ValueError('The requested time increment is too small to advance simulation time.')
            trial_step = target-current
            self.attempts += 1
            try:
                if self.travel == 0:
                    candidate_x, candidate, candidate_metric = x, state, dict(metric)
                else:
                    candidate_x, candidate, candidate_metric = self.system.solve(self.payouts*schedule.at(target), x)
                crossing = np.any((state['positions'][:, 2] < 0) != (candidate['positions'][:, 2] < 0))
                active_change = np.any(state['active'] != candidate['active'])
                surface_change = abs(candidate['buoyancy']-state['buoyancy']) > max(10.0, .01*abs(state['buoyancy']))
                event = bool(crossing or active_change or surface_change)
                if event and trial_step > self.minimum*(1+1e-9):
                    step = max(self.minimum, trial_step/2)
                    self.retries += 1
                    continue
            except EquilibriumError:
                if trial_step <= self.minimum*(1+1e-9):
                    raise
                step = max(self.minimum, trial_step/2)
                self.retries += 1
                continue
            increment = abs(schedule.at(target)-schedule.at(current))*self.travel
            current, x, state, metric = target, candidate_x, candidate, candidate_metric
            self.accepted += 1
            self.events += int(event)
            if save_interval is None or current == deadline:
                yield current, state, dict(metric, incrementM=increment, event=event)
                if save_interval is not None:
                    save_number += 1
            step = min(self.limit, trial_step*(1.25 if event else 1.5))
