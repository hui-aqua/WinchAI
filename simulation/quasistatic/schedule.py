"""Integrate piecewise-linear path velocities exactly, without dynamics."""
import math
import numpy as np


class VelocitySchedule:
    def __init__(self, duration, travel, profile=None):
        self.duration, self.travel = duration, travel
        self.profile = profile
        if profile is None:
            return
        if not isinstance(profile, list) or len(profile) < 2:
            raise ValueError('Velocity profile needs at least two time/velocity samples.')
        for row in profile:
            if not isinstance(row, dict) or any(isinstance(row.get(k), bool) or
                not isinstance(row.get(k), (int, float)) or not math.isfinite(row[k])
                for k in ('timeS', 'velocityMPerS')):
                raise ValueError('Velocity profile samples require finite timeS and velocityMPerS.')
        self.times = np.array([r['timeS'] for r in profile])
        self.velocities = np.array([r['velocityMPerS'] for r in profile])
        if self.times[0] != 0 or self.times[-1] != duration or np.any(np.diff(self.times) <= 0):
            raise ValueError('Velocity profile times must increase from zero to the run duration.')
        dt = np.diff(self.times)
        self.distances = np.r_[0, np.cumsum(dt*(self.velocities[:-1]+self.velocities[1:])/2)]
        extrema = list(self.distances)
        for i, delta in enumerate(dt):
            v0, v1 = self.velocities[i:i+2]
            if v0*v1 < 0:
                t = -v0*delta/(v1-v0)
                extrema.append(self.distances[i]+v0*t+(v1-v0)*t*t/(2*delta))
        epsilon = 1e-9*max(1, travel)
        if min(extrema) < -epsilon or max(extrema) > travel+epsilon:
            raise ValueError('Velocity profile leaves the precomputed rope-length range.')

    def at(self, time):
        if self.profile is None:
            return time/self.duration if self.duration else 0.0
        i = min(int(np.searchsorted(self.times, time, side='right'))-1, len(self.times)-2)
        dt = time-self.times[i]
        slope = (self.velocities[i+1]-self.velocities[i])/(self.times[i+1]-self.times[i])
        distance = self.distances[i]+self.velocities[i]*dt+slope*dt*dt/2
        return float(np.clip(distance/self.travel, 0, 1)) if self.travel else 0.0
