"""Zero-sum differential axial thrust for the still-attached vehicle."""
import numpy as np

def request_moments(error, rate, ascent_trim):
    """Reuse only pitch asymmetry; ascent lateral trim is maneuver-specific.

    return36 inherited Z=-3.893 from the ascent turn and consequently
    requested a spurious Z moment of about-8 during the first orbital pulse.
    """
    feedforward=np.array([ascent_trim[0],0.,0.])
    return 2*(5*np.asarray(error)-6*np.asarray(rate)+feedforward)[[0,2]],feedforward

def distribute(points, collective, requested_moment, limit=.6):
    """Retain sum of thrust settings; scale correction to native bounds.

    All engines thrust along +bodyY. requested_moment is body X/Z in
    command-times-distance units, not a calibrated physical torque.
    Native velocity-dependent attenuation can change actual total force
    under redistribution, even though the sum of settings is conserved.
    """
    points=np.asarray(points,dtype=float)
    collective=float(collective)
    if not 0<=collective<=limit:raise ValueError('Collective outside bounds')
    centered=points-points.mean(axis=0)
    matrix=np.vstack([np.ones(len(points)),-centered[:,2],centered[:,0]])
    correction=np.linalg.pinv(matrix)@np.r_[0.,requested_moment]
    scale=1.
    for delta in correction:
        if delta>1e-12:scale=min(scale,(limit-collective)/delta)
        elif delta < -1e-12:scale=min(scale,-collective/delta)
    power=np.clip(collective+scale*correction,0.,limit)
    return power,dict(scale=float(scale),requested_moment=np.asarray(requested_moment).tolist(),
                      applied_moment=(matrix@power)[1:].tolist(),
                      total_power=float(power.sum()),individual_limit=limit)
