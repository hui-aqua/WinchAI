"""Validated simulation-dialog request settings shared by API and CLI."""
import math


def number(value, name, *, positive=False):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError(f'{name} must be a finite number.')
    if positive and value <= 0:
        raise ValueError(f'{name} must be positive.')
    return value


def parse_controls(data):
    if not isinstance(data,dict):
        raise ValueError('Simulation settings must be an object.')
    duration = number(data.get('durationSeconds'),'Duration',positive=True)
    interval = number(data.get('saveIntervalSeconds'),'Save ΔT',positive=True)
    increment = number(data.get('initialIncrementM',0.05),'Initial increment',positive=True)
    if not 0.001 <= increment <= 0.05:
        raise ValueError('Initial increment must be between 1 and 50 mm.')
    overrides = data.get('components',[])
    if not isinstance(overrides,list):
        raise ValueError('Component properties must be an array.')
    for row in overrides:
        if not isinstance(row,dict):
            raise ValueError('Each component override must be an object.')
        for key,value in row.items():
            number(value,f'Component {key}')
    winches = data.get('winches')
    if not isinstance(winches,list) or not winches:
        raise ValueError('Winch controls must be a nonempty array.')
    payouts = {}
    for row in winches:
        if not isinstance(row,dict):
            raise ValueError('Each winch control must be an object.')
        cid = row.get('componentId')
        if isinstance(cid,bool) or not isinstance(cid,int) or cid in payouts:
            raise ValueError('Winch controls have invalid or duplicate IDs.')
        payout = number(row.get('payoutM'),'Specified payout')
        if abs(payout) > 10000:
            raise ValueError('Specified payout must be between -10,000 and 10,000 m.')
        payouts[cid] = payout
    return dict(duration_seconds=duration,save_interval_seconds=interval,
                initial_increment_m=increment,component_overrides=overrides,winch_payouts=payouts)
