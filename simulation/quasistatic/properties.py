"""Editable, effective simulation properties in SI units, grouped by component."""
import math
import numpy as np
from simulation.model_io import GRAVITY_M_S2 as G
from .model import connected_groups


def component_properties(model):
    rows = []
    for component in model.metadata['components']:
        cid = component['componentId']
        rigid = component['type'] == 'beam'
        segments = [e for e in (model.beams if rigid else model.edges) if e['componentId'] == cid]
        length = sum(e['length'] for e in segments)
        mass = sum(e['mass'] if rigid else e['air']*e['length'] for e in segments)
        water_weight = sum(e['mass']*G-e['buoyancy'] if rigid else e['water']*e['length']*G for e in segments)
        row = dict(component, lengthM=float(length), eaN=None if rigid or not segments else float(segments[0]['ea']),
                   massKg=float(mass), weightInAirN=float(mass*G), weightInWaterN=float(water_weight))
        if cid in {w['componentId'] for w in model.metadata['winches']}:
            row['pathLengthsM'] = [float(sum(segments[i]['length'] for i in group)) for group in connected_groups(segments)]
        rows.append(row)
    return rows


def apply_component_overrides(model, overrides=None):
    baseline = {r['componentId']:r for r in component_properties(model)}
    seen = set()
    for override in overrides or []:
        if not isinstance(override, dict):
            raise ValueError('Each component override must be an object.')
        cid = override.get('componentId')
        if isinstance(cid, bool) or not isinstance(cid, int) or cid not in baseline or cid in seen:
            raise ValueError('Component overrides contain unknown or duplicate component IDs.')
        seen.add(cid)
        allowed = {'componentId','eaN','lengthM','massKg','weightInAirN','weightInWaterN'}
        if set(override)-allowed:
            raise ValueError(f'Unknown property for component {cid}.')
        for key,value in override.items():
            if key == 'componentId':
                continue
            if isinstance(value, bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
                raise ValueError(f'Component {cid}: {key} must be finite.')
            if key in {'eaN','lengthM'} and value <= 0:
                raise ValueError(f'Component {cid}: {key} must be positive.')
            if key in {'massKg','weightInAirN'} and value < 0:
                raise ValueError(f'Component {cid}: {key} must be nonnegative.')
        row = baseline[cid]
        rigid = row['type'] == 'beam'
        if rigid and ('eaN' in override or 'lengthM' in override):
            raise ValueError('Rigid beam EA and geometric length cannot be overridden.')
        length = override.get('lengthM',row['lengthM'])
        if length <= 0:
            raise ValueError(f'Component {cid} has no supported simulation segments.')
        mass = override.get('massKg',override.get('weightInAirN',row['weightInAirN'])/G)
        if 'massKg' in override and 'weightInAirN' in override and not math.isclose(mass*G,override['weightInAirN'],rel_tol=1e-6,abs_tol=1e-6):
            raise ValueError('Mass and air weight must satisfy W = mass × gravity.')
        water = override.get('weightInWaterN',row['weightInWaterN'])
        if water > mass*G+1e-6:
            raise ValueError(f'Component {cid}: water weight cannot exceed air weight.')
        for segment in (model.beams if rigid else model.edges):
            if segment['componentId'] != cid:
                continue
            if rigid:
                share = segment['length']/length
                segment['mass'] = mass*share
                segment['buoyancy'] = (mass*G-water)*share
            else:
                segment['length'] *= length/row['lengthM']
                segment['ea'] = override.get('eaN',segment['ea'])
                segment['air'] = mass/length
                segment['water'] = water/G/length
    # Keep mass metadata and rigid pose references consistent with edited weights.
    for row in model.metadata['beamLoads']:
        segments = [b for b in model.beams if b['componentId'] == row['componentId']]
        row['massKg'] = sum(b['mass'] for b in segments)
        row['weightN'] = row['massKg']*G
        row['buoyancyN'] = sum(b['buoyancy'] for b in segments)
        row['massPerMkg'] = row['massKg']/row['lengthM']
        row['submergedMassPerMkg'] = (row['weightN']-row['buoyancyN'])/G/row['lengthM']
    mass = sum(b['mass'] for b in model.beams)
    if mass > 0:
        model.metadata['centerOfMassM'] = (sum(b['mass']*(model.positions[b['a']]+model.positions[b['b']])/2 for b in model.beams)/mass).tolist()
    model.metadata['totalMassKg'] = float(mass)
    model.metadata['weightN'] = float(mass*G)
    model.metadata['buoyancyN'] = float(sum(b['buoyancy'] for b in model.beams))
    model.metadata['netVerticalLoadN'] = model.metadata['weightN']-model.metadata['buoyancyN']-float(model.forces[:,2].sum())
    for i,body in enumerate(model.bodies):
        segments = [b for b in model.beams if model.owner[b['a']] == i]
        body_mass = sum(b['mass'] for b in segments)
        if body_mass > 0:
            body.center = sum(b['mass']*(model.positions[b['a']]+model.positions[b['b']])/2 for b in segments)/body_mass
            body.local = model.positions[body.nodes]-body.center
            body.scale = max(1.0,float(np.linalg.norm(body.local,axis=1).max()))
    model.metadata['componentProperties'] = component_properties(model)
