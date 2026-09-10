"""Spherical landing-field planning; all post-release control is aerodynamic."""
import numpy as np
from scipy.spatial.transform import Rotation as R

GM=8456800.
CENTER=np.array([0.,-600.,0.])
GLIDE_ARC=4.175  # V1 measured entry-to-rest angular travel, including entry500.

def unit(v):return v/max(np.linalg.norm(v),1e-9)

def entry_direction(p,v,radius=1100.):
    rel=p-CENTER; normal=unit(np.cross(rel,v)); momentum=np.linalg.norm(np.cross(rel,v))
    evec=np.cross(v,np.cross(rel,v))/GM-unit(rel); e=np.linalg.norm(evec)
    if e<1e-6:return None
    cosine=(momentum*momentum/(GM*radius)-1)/e
    if abs(cosine)>1:return None
    peri=unit(evec); along=np.cross(normal,peri)
    return cosine*peri-np.sqrt(max(0.,1-cosine*cosine))*along,normal

def coast_predict(p,v,seconds):
    # Small deterministic RK4 forecast used only to schedule the burn window.
    state=np.r_[p-CENTER,v]
    def derivative(s):return np.r_[s[3:],-GM*s[:3]/np.linalg.norm(s[:3])**3]
    dt=seconds/8
    for _ in range(8):
        a=derivative(state);b=derivative(state+a*dt/2);c=derivative(state+b*dt/2);d=derivative(state+c*dt)
        state+=dt*(a+2*b+2*c+d)/6
    return state[:3]+CENTER,state[3:]

def burn_window(p,v,launch,apo_radius,lead_seconds=22.):
    from return_staging import outbound_velocity
    p,v=coast_predict(p,v,lead_seconds)
    rad=unit(p-CENTER);radius=np.linalg.norm(p-CENTER)
    if not 900<radius<apo_radius:return None
    target=outbound_velocity(radius,rad,unit(v-rad*(v@rad)),900.,apo_radius)
    entry=entry_direction(p,target)
    if entry is None:return None
    point,normal=entry
    landing=R.from_rotvec(normal*GLIDE_ARC).apply(point)
    error=float(np.arctan2(launch@np.cross(normal,landing),launch@landing))
    return dict(phase_error=error,crossrange=600*float(np.arcsin(np.clip(launch@normal,-1,1))),
                predicted_landing=landing.tolist(),lead_seconds=lead_seconds)

def field_bank(rad,velocity,launch,height,route_normal=None):
    """Turn toward the target orbital plane; preserve forward long-arc travel."""
    tangent=velocity-rad*(velocity@rad);speed=np.linalg.norm(tangent);forward=unit(tangent)
    right=unit(np.cross(forward,rad));toward=unit(launch-rad*(launch@rad))
    angle=float(np.arccos(np.clip(rad@launch,-1,1)))
    # A 237-degree glide initially follows the long route around the sphere.
    if angle>np.pi/2 and toward@forward<0:toward=-toward
    if route_normal is not None and angle>1.:
        # A fixed great-circle route has no heading reversal at the antipode.
        cross_track=float(np.arcsin(np.clip(rad@route_normal,-1,1)))
        course=unit(np.cross(route_normal,rad))
        toward=unit(course-(route_normal-rad*(route_normal@rad))*np.clip(4*cross_track,-.5,.5))
    heading=float(np.arctan2(toward@right,toward@forward))
    lookahead=max(100.,speed*8.)
    lateral=2*speed*speed/lookahead*np.sin(heading)
    bank=float(np.clip(np.arctan2(lateral,22.),-np.deg2rad(25),np.deg2rad(25)))
    bank*=float(np.clip((height-10)/50,0,1))
    return bank,dict(field_distance=600*angle,heading_error_deg=float(np.degrees(heading)),bank_target_deg=float(np.degrees(bank)))
