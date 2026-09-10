"""Reduce opposed rotor speed while retaining each pair's signed sum.

This is a command-space equal-inertia construction, not a calibrated momentum
model. Actual motor response and body disturbance still need live verification.
"""
import numpy as np

def reduce_opposed_spin(baselines, axes, dt, floor=.05, slew=.04):
    pairs={i:[] for i in range(3)}
    for g,axis in axes.items():
        i=int(np.argmax(abs(axis)))
        if abs(axis[i])<.999:raise ValueError('Expected orthogonal wheel axes')
        pairs[i].append((g,float(np.sign(axis[i]))))
    if any(len(v)!=2 for v in pairs.values()):raise ValueError('Expected two wheels per axis')
    result=dict(baselines)
    for pair in pairs.values():
        (a,sa),(b,sb)=pair
        x,y=baselines[a]*sa,baselines[b]*sb
        mean=(x+y)/2;opposed=(x-y)/2
        # Do not inject spin into an already slow pair; only remove excess.
        reduction=min(max(abs(opposed)-floor,0.),slew*max(dt,0.))
        reduced=opposed-np.sign(opposed)*reduction
        result[a]=(mean+reduced)*sa;result[b]=(mean-reduced)*sb
    return result
