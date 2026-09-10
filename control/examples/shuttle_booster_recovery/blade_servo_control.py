"""Track native SteeringWheel target integration instead of chasing joint lag."""
import numpy as np

class BladeServoControl:
    def __init__(self):
        self.last=None;self.targets={};self.rates={}

    def update(self,t,angles,goals,surfaces,signs,guid,sliders,commands):
        dt=.08 if self.last is None else float(np.clip(t-self.last,.001,.2))
        self.last=t
        for name,(servo,blade) in surfaces.items():
            if name not in self.targets:self.targets[name]=angles[name]
            self.targets[name]+=self.rates.get(name,0.)*dt
            # File-action/physics timing can lose a fraction of a pulse.
            # Bounded slow measurement correction removes this offset without
            # driving the native target rapidly through the joint's lag.
            innovation=float(np.clip(angles[name]-self.targets[name],-np.deg2rad(1),np.deg2rad(1)))
            self.targets[name]+=innovation*min(1.,dt/.3)
            delta=goals[name]-self.targets[name]
            rate=float(np.clip(delta/max(dt,.08),-np.deg2rad(40),np.deg2rad(40)))
            if abs(delta)<np.deg2rad(.15):rate=0.
            # Native version0 SteeringBlock targetAngleSpeed=.8 =>80deg/s at1.
            speed=abs(rate)/np.deg2rad(80)
            sliders.extend([(guid(servo),'rotation-speed',speed),(guid(servo),'tension',2.)])
            if rate:
                commands.append((guid(servo),'leftKey' if rate*signs[name]>0 else 'rightKey'))
            self.rates[name]=rate
        return dict(native_target_estimate=self.targets.copy(),native_target_rate=self.rates.copy())
