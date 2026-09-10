"""Optional orbital wheel identification using native motor inputs only."""
import os
import numpy as np
from scipy.spatial.transform import Rotation as R

class WheelResponse:
    def __init__(self, staging):
        self.s=staging
        self.start=None
        self.sequence=[(axis,speed) for speed in (.005,.02,.05) for axis in range(3)]
        self.cap_probe=os.environ.get('BAILU_WHEEL_CAP_PROBE')=='1'
        if self.cap_probe:self.sequence=[(0,speed) for speed in (.05,.15,.3,.5)]

    def update(self,t,d,sliders,commands):
        if self.start is None:
            self.start=t
            self.s.events.append(dict(t=t,event='wheel_response_started'))
        elapsed=t-self.start
        # Preserve the ascent controller's active damping during initial
        # settling. Zero motor speed alone does not remove residual body spin.
        if self.cap_probe and elapsed<20.:
            return dict(elapsed=elapsed,settling=True,cap_probe=False)
        offset=20. if self.cap_probe else 10.
        period=12. if self.cap_probe else 6.
        index=int(max(0.,elapsed-offset)//period)
        phase=(elapsed-offset)%period
        axis,speed=self.sequence[min(index,len(self.sequence)-1)]
        active=elapsed>=offset and index<len(self.sequence) and phase<(6. if self.cap_probe else 1.)
        wheels=self.s.all_wheels
        sliders[:]=[(g,k,v) for g,k,v in sliders if g not in wheels]
        commands[:]=[(g,k) for g,k in commands if g not in wheels]
        for g in wheels:
            projection=(-self.s.saved[g].apply([0,0,1]))[axis]
            selected=active and abs(projection)>.9
            sliders.extend([(g,'speed',speed if selected else (0. if self.cap_probe else .5)),
                            (g,'damper',0. if self.cap_probe else .5)])
            if selected:commands.append((g,'LeftKey' if projection>0 else 'RightKey'))
        core=self.s.cores[0]
        actual=R.from_quat(d[core]['rotation'])*self.s.saved[core].inv()
        return dict(elapsed=elapsed,index=index,axis=axis,speed=speed,active=active,
                    cap_probe=self.cap_probe,
                    omega_body=actual.inv().apply(d[core]['angular_velocity']).tolist(),
                    wheel_omega_body={g:actual.inv().apply(d[g]['angular_velocity']).tolist() for g in wheels})
