"""Bounded physical wrench allocation for fan / lift rocket / axial rocket.

Coefficients must be loaded from the applicable calibrated specimen.
"""
import numpy as np
from scipy.optimize import lsq_linear
from scipy.spatial.transform import Rotation as R


class AttitudeGovernor:
    """Continuous ordinary-flight reference with bounded rate/acceleration.

    Acrobatic and flare references have their own analytic derivatives and
    bypass this governor, preserving their intentional rapid rotations.
    """
    def __init__(self):
        self.rotation=None
        self.rate=np.zeros(3)

    def step(self,target,actual,omega,dt,reset=False):
        if self.rotation is None or reset:
            self.rotation=actual
            self.rate=np.array(omega,dtype=float)
        duration=max(.001,dt);steps=max(1,int(np.ceil(duration/.02)))
        h=duration/steps;acc=np.zeros(3)
        for _ in range(steps):
            error=(target*self.rotation.inv()).as_rotvec()
            acc=36*error-12*self.rate
            acc*=min(1.,5/max(np.linalg.norm(acc),1e-8))
            self.rate+=acc*h
            self.rate*=min(1.,1.5/max(np.linalg.norm(self.rate),1e-8))
            self.rotation=R.from_rotvec(self.rate*h)*self.rotation
        return self.rotation,self.rate.copy(),acc


def allocate(force_body,alpha_body,points,kinds,model,attitude_weight=3.,rocket_penalty=.4,upper=None):
    kfan=model['thrust_acceleration_per_fan_power']
    klift=model['lift_rocket_acceleration_per_engine_power'] if 'lift_rocket_acceleration_per_engine_power' in model else model['four_lift_rocket_acceleration_per_power']/4
    kforward=model.get('axial_rocket_acceleration_per_engine_power',model['paired_rocket_acceleration_per_power']/2)
    # Roll/pitch inverse inertia estimates per acceleration moment, awaiting
    # large-angle validation on this specimen. Axial height offsets included.
    columns=[]
    for point,kind in zip(points,kinds):
        r=np.asarray(point)-np.array([0,.45,-.28])
        f=np.array([0,kfan if kind=='fan' else klift,0]) if kind!='axial' else np.array([0,0,kforward])
        m=np.cross(r,f)
        columns.append([f[1],f[2],.115*m[0],.115*m[2]])
    A=np.array(columns).T;weights=np.array([1,1,attitude_weight,attitude_weight])
    b=np.array([force_body[1],force_body[2],alpha_body[0],alpha_body[2]])
    # Prefer renewable rotor power; finite penalty must never hide residuals.
    reg=np.array([.08 if kind=='fan' else rocket_penalty for kind in kinds])
    opt=lsq_linear(np.vstack([weights[:,None]*A,np.diag(reg)]),np.r_[weights*b,np.zeros(len(kinds))],bounds=(0,2 if upper is None else np.maximum(upper,1e-9)),tol=1e-6,max_iter=40)
    return opt.x,A@opt.x-b


def attitude(force,forward=(0,0,1)):
    up=np.array(force,dtype=float);up/=max(np.linalg.norm(up),1e-8)
    right=np.cross(up,forward);right/=max(np.linalg.norm(right),1e-8)
    return R.from_matrix(np.column_stack([right,up,np.cross(right,up)]))


class Helix:
    def __init__(self,origin,radius=40,forward=25,gravity=32,load=1.4,ramp=0.,turns=1):
        self.origin=np.array(origin);self.radius=radius;self.forward=forward
        self.w=np.sqrt(load*gravity/radius);self.ramp=ramp;self.duration=turns*2*np.pi/self.w+ramp/2

    def sample(self,t):
        r=self.radius;w=self.w;alpha=0.
        if self.ramp and t<self.ramp:
            tau=self.ramp;s=t/tau;th=w*tau*(2.5*s**4-3*s**5+s**6)
            alpha=w/tau*(30*s**2-60*s**3+30*s**4);w=w*(10*s**3-15*s**4+6*s**5)
        else:th=w*(t-self.ramp/2)
        p=self.origin+[r*np.sin(th),r*(1-np.cos(th)),self.forward*t]
        v=np.array([r*w*np.cos(th),r*w*np.sin(th),self.forward])
        a=np.array([r*alpha*np.cos(th)-r*w*w*np.sin(th),r*alpha*np.sin(th)+r*w*w*np.cos(th),0])
        return p,v,a


class Join:
    def __init__(self,p0,v0,a0,p1,v1,a1,duration):
        self.duration=T=duration
        c0=np.array(p0);c1=np.array(v0)*T;c2=np.array(a0)*T*T/2
        tail=np.linalg.solve([[1,1,1],[3,4,5],[6,12,20]],np.array([np.array(p1)-c0-c1-c2,np.array(v1)*T-c1-2*c2,np.array(a1)*T*T-2*c2]))
        self.c=np.vstack([c0,c1,c2,tail])

    def sample(self,t):
        s=np.clip(t/self.duration,0,1);c=self.c;T=self.duration
        return np.array([s**i for i in range(6)])@c,np.array([0]+[i*s**(i-1)/T for i in range(1,6)])@c,np.array([0,0]+[i*(i-1)*s**(i-2)/T**2 for i in range(2,6)])@c
