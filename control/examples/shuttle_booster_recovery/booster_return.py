"""Independent spherical RTLS guidance; touchdown still needs runtime evidence."""
import numpy as np
import os
from scipy.spatial.transform import Rotation as R

CENTER=np.array([0.,-600.,0.])
GM=8456800.

def unit(x):
    return x/max(np.linalg.norm(x),1e-9)

def approach_speed_limit(surface_distance, radius, acceleration=3., attitude_delay=3.):
    """Reserve travel during attitude response, then brake along the flight arc."""
    flight_distance=max(0.,surface_distance)*radius/600.
    return float(np.sqrt((acceleration*attitude_delay)**2+2*acceleration*flight_distance)
                 -acceleration*attitude_delay)

def preterminal_descent_speed(common_height, above_pad, distance, horizontal_speed):
    """Approach the shared20-height capture region with braking reserve.

    return48 crawled at2m/s hundreds of units above the pad and ran out of fuel.
    Slow pad descent belongs near the ground; each booster independently reserves
    distance to brake even when its partner is still above the terminal window.
    """
    target=float(np.clip((common_height-above_pad)*.25,-45,18))
    if distance<8 and horizontal_speed<3 and above_pad<30:
        target=-min(2.,max(.25,above_pad*.4))
    braking_limit=np.sqrt(6.*max(0.,above_pad-20.))
    target=max(target,-braking_limit)
    # Approach the existing15m/s lower-atmosphere limit continuously. Reserve
    # 3m/s^2 net braking above250 instead of changing the target abruptly there.
    atmosphere_limit=np.sqrt(15.**2+6.*max(0.,above_pad-250.))
    target=max(target,-atmosphere_limit)
    return float(target)

def terminal_hold_speed(above_pad):
    """Descend to20 without abruptly demanding3m/s at the60-height latch."""
    target=float(np.clip((20.-above_pad)*.6,-12.,3.))
    return max(target,-float(np.sqrt(6.*max(0.,above_pad-20.))))

class BoosterReturn:
    def __init__(self, staging, cruise_height=None):
        self.s=staging
        self.cruise_height=float(os.environ.get('BAILU_RTLS_CRUISE_HEIGHT','600')) if cruise_height is None else float(cruise_height)
        self.max_lateral_speed=float(os.environ.get('BAILU_RTLS_MAX_SPEED','45'))
        self.fine_height=float(os.environ.get('BAILU_RTLS_FINE_HEIGHT','150'))
        self.pads=None
        self.integral={side:0. for side in (-1,1)}
        self.last_t=None
        self.reference_height={}
        self.previous={}
        self.acceleration_per_power={side:10. for side in (-1,1)}
        self.capturing=True
        self.contact_since={s:None for s in (-1,1)}
        self.cutoff={s:None for s in (-1,1)}
        self.terminal_started=None
        self.final_ready_since=None
        self.final_descent_started=None
        self.fine_attitude={s:False for s in (-1,1)}
        self.attitude_integral={s:np.zeros(3) for s in (-1,1)}

    def observe_launch(self,d):
        # Core height above the local surface includes actual settled geometry.
        if not self.reference_height:
            for side in (-1,1):
                core=self.s.cores[side]
                self.reference_height[side]=float(np.linalg.norm(np.array(d[core]['position'])-CENTER)-600)

    def update(self,t,d,sliders,commands):
        dt=.04 if self.last_t is None else np.clip(t-self.last_t,.001,.2)
        self.last_t=t
        if self.pads is None:
            # Two separated touchdown locations at the launch site. They are
            # expressed on the actual sphere, rather than a flat world plane.
            crossrange=np.cross(self.s.launch,[1.,0.,0.])
            crossrange=unit(crossrange)
            # return17 began with R-L projection -26 along this axis but
            # assigned R to the positive pad. The routes crossed and block
            # origins passed within3.23. Preserve observed lateral ordering.
            relative=np.array(d[self.s.cores[1]]['position'])-np.array(d[self.s.cores[-1]]['position'])
            if float(relative@crossrange)<0:
                crossrange=-crossrange
            self.pads={side:unit(self.s.launch+side*crossrange*20/600) for side in (-1,1)}
            self.s.events.append(dict(t=t,event='landing_pads_assigned_without_lateral_swap',
                                      initial_right_minus_left_projection=float(relative@crossrange),
                                      pad_directions={str(side):point.tolist() for side,point in self.pads.items()}))
        state={}
        for side in (-1,1):
            core=self.s.cores[side]
            p=np.array(d[core]['position']);v=np.array(d[core]['velocity'])
            radius=np.linalg.norm(p-CENTER);rad=(p-CENTER)/radius
            angle=float(np.arccos(np.clip(rad@self.pads[side],-1,1)))
            state[side]=(p,v,radius,rad,600*angle)
        # Shared altitude schedule keeps both descent profiles synchronized;
        # lateral tracking and thrust remain independent.
        common_height=float(np.clip(.8*max(x[4] for x in state.values()),0,self.cruise_height))
        if max(x[4] for x in state.values())>=12:common_height=max(common_height,60.)
        max_horizontal_speed=max(np.linalg.norm(x[1]-x[3]*(x[1]@x[3])) for x in state.values())
        # Do not enter the low-altitude, three-unit lateral authority region
        # while either vehicle still needs substantial horizontal braking.
        if max_horizontal_speed>10.:common_height=max(common_height,110.)
        pad_heights={s:x[2]-600-self.reference_height[s] for s,x in state.items()}
        minimum_vehicle_height=min(pad_heights.values())
        if self.capturing:
            captured=all(pad_heights[s]>.75*self.cruise_height and float(x[1]@x[3])>-2 and
                         (R.from_quat(d[self.s.cores[s]]['rotation'])*self.s.saved[self.s.cores[s]].inv()).apply([0,1,0])@x[3]>.95
                         for s,x in state.items())
            if captured:
                self.capturing=False
                self.s.events.append(dict(t=t,event='both_boosters_descent_arrested'))
            else:common_height=max(self.cruise_height,max(pad_heights.values()))
        terminal=max(x[4] for x in state.values())<12 and max(pad_heights.values())<60 and max_horizontal_speed<3.
        if terminal and self.terminal_started is None:
            self.terminal_started=t
            self.s.events.append(dict(t=t,event='synchronized_terminal_latched'))
        terminal=self.terminal_started is not None
        if terminal and self.final_descent_started is None:
            aligned=all(x[4]<6 and pad_heights[s]<25 and
                        np.linalg.norm(x[1]-x[3]*(x[1]@x[3]))<.75 and
                        np.linalg.norm(d[self.s.cores[s]]['angular_velocity'])<.15
                        for s,x in state.items())
            if aligned:
                if self.final_ready_since is None:self.final_ready_since=t
                if t-self.final_ready_since>.5:
                    self.final_descent_started=t
                    self.s.events.append(dict(t=t,event='both_boosters_final_descent_ready'))
            else:self.final_ready_since=None
        if terminal:
            # Bring the lower booster toward the higher one's descent path.
            common_height=max(-.5,min(pad_heights.values())-2.)
        report={}
        for side,(p,v,radius,rad,distance) in state.items():
            core=self.s.cores[side]
            actual=R.from_quat(d[core]['rotation'])*self.s.saved[core].inv()
            up=actual.apply([0,1,0]);vr=float(v@rad);vt=v-vr*rad
            gravity=min(22.,GM/radius**2)
            previous=self.previous.get(side)
            if previous and previous[2]>.3 and (radius>1120 or (radius>610 and np.linalg.norm(v)<10)):
                measured=((v-previous[0])/dt+gravity*rad)@previous[1]/previous[2]
                if 3<measured<30:
                    self.acceleration_per_power[side]=.95*self.acceleration_per_power[side]+.05*measured
            above_pad=radius-600-self.reference_height[side]
            target_vr=preterminal_descent_speed(common_height,above_pad,distance,max_horizontal_speed)
            if terminal:
                # Descend together toward the lower vehicle instead of making
                # it climb and burning the landing reserve while hovering.
                base_descent=min(3.,max(.4,above_pad*.35))
                catchup=min(5.,.5*max(0.,above_pad-minimum_vehicle_height))
                target_vr=-min(8.,base_descent+catchup)
                # Catch-up must yield to a braking envelope near the ground.
                # return09's higher booster still descended6.7m/s below12.
                stopping_limit=.3+np.sqrt(3.*max(0.,above_pad-.5))
                target_vr=max(target_vr,-stopping_limit)
                if self.final_descent_started is None:
                    # Establish both low lateral speed and pad alignment before
                    # descending through the last20 units (return12 drifted).
                    target_vr=terminal_hold_speed(above_pad)
            if above_pad<250:target_vr=max(target_vr,-15.)
            direction=unit(self.pads[side]-rad*(self.pads[side]@rad))
            braking_speed_limit=approach_speed_limit(distance,radius,acceleration=1.5,attitude_delay=6.)
            target_vt=direction*min(self.max_lateral_speed,distance*.15,braking_speed_limit)
            if terminal:
                target_vt=direction*min(2.,distance*.2)
                if self.final_descent_started is not None:
                    target_vt=direction*min(.7,distance*.15)
            if self.capturing:target_vt=vt.copy()
            self.integral[side]=float(np.clip(self.integral[side]+(target_vr-vr)*dt*.15,-6,6))
            # At orbital speed inward thrust may be necessary: return30's
            # outward-only floor prevented capture and raised apoapsis.
            accel=rad*(gravity-(vt@vt)/radius+np.clip((target_vr-vr)*2+self.integral[side],-30,30))
            # Native wheel/body response takes seconds. The original1.3 gain
            # chased velocity faster than attitude could follow near the field.
            lateral=(target_vt-vt)*.35
            if above_pad<80:
                lateral*=min(1.,3./max(np.linalg.norm(lateral),1e-9))
            accel+=np.clip(lateral,-40,40)
            wanted=unit(accel)
            # Use nose-axis control and roll damping on the booster. Avoid
            # a 180-degree singularity by explicitly using a perpendicular axis.
            error=np.cross(up,wanted)
            if up@wanted<-.95:
                error=actual.apply([1.,0.,0.])
            omega=np.array(d[core]['angular_velocity'])
            torque=actual.inv().apply(1.2*error-2.4*omega)
            if above_pad<self.fine_height and up@rad>.95 and not self.capturing:
                self.fine_attitude[side]=True
            local_error=actual.inv().apply(error)
            local_omega=actual.inv().apply(omega)
            trim_delta=np.zeros(3)
            if self.fine_attitude[side] and np.linalg.norm(error)<.25 and np.linalg.norm(omega)<.15:
                trim_delta=np.clip(self.attitude_integral[side]+.05*local_error*dt,-.15,.15)-self.attitude_integral[side]
            motor_report={}
            for g in self.s.wheels[side]:
                if self.fine_attitude[side]:
                    # Preserve measured rotor momentum instead of repeatedly
                    # commanding full speed then braking to zero near a pad.
                    # The native target is1200deg/s at unit slider speed;
                    # observed rotor rates reach the native5rad/s ceiling.
                    axis=-self.s.saved[g].apply([0,0,1])
                    relative=actual.inv().apply(np.array(d[g]['angular_velocity'])-omega)
                    baseline=float(np.clip(-relative@axis/np.radians(1200.),-.238,.238))
                    # return27's .245 cap could not recover when the rotor
                    # was saturated and the booster started tipping. Retain
                    # the established .7 pitch authority for large corrections.
                    output_cap=.3 if abs(axis[1])>.9 else .7
                    # hop04 held a persistent ~7-unit pad error with tiny
                    # angular velocity: proportional attitude error supplied
                    # the steady torque needed by the asymmetric booster.
                    # Integrate small attitude error to supply this trim while
                    # allowing the guidance tilt itself to be achieved.
                    trim_step=float(trim_delta@axis)
                    candidate=float((.8*local_error-2.4*local_omega+self.attitude_integral[side])@axis)+trim_step
                    if abs(baseline+candidate)<=output_cap or trim_step*(baseline+candidate)<0:
                        self.attitude_integral[side]+=axis*trim_step
                    correction=float((.8*local_error-2.4*local_omega+self.attitude_integral[side])@axis)
                    value=float(np.clip(baseline+correction,-output_cap,output_cap))
                    sliders.extend([(g,'speed',abs(value)),(g,'damper',0.)])
                    commands.append((g,'LeftKey' if value>=0 else 'RightKey'))
                    motor_report[g]=dict(baseline=baseline,correction=correction,command=value,output_cap=output_cap)
                    continue
                val=torque@(-self.s.saved[g].apply([0,0,1]))
                # Longitudinal spin damping needs far less authority than
                # pitch control; fixed.7 drove +/-1rad/s yaw in hop02.
                cap=.06 if abs((-self.s.saved[g].apply([0,0,1]))[1])>.9 else .5
                wheel_speed=float(np.clip(2*abs(val),0.,cap))
                sliders.extend([(g,'speed',wheel_speed),(g,'damper',.5)])
                if abs(val)>.025:commands.append((g,'LeftKey' if val>0 else 'RightKey'))
            power=float(np.clip((accel@up)/(len(self.s.engines[side])*self.acceleration_per_power[side]),0,2))
            if up@wanted<.7:power=0.
            side_name='L' if side==-1 else 'R'
            pad_guids=[self.s.guid('pad'+side_name+n) for n in ('North','South','West' if side==-1 else 'East')]
            pad_heights=[float(np.linalg.norm(np.array(d[g]['position'])-CENTER)-600) for g in pad_guids]
            near_pads=sum(h<.75 and abs(np.array(d[g]['velocity'])@rad)<.35 for g,h in zip(pad_guids,pad_heights))
            grounded=near_pads>=2 and abs(vr)<.35 and np.linalg.norm(vt)<3. and up@rad>.98 and np.linalg.norm(omega)<.3
            at_target=distance<8
            if grounded:
                if self.contact_since[side] is None:self.contact_since[side]=t
                if t-self.contact_since[side]>.25 and self.cutoff[side] is None:
                    self.cutoff[side]=t
                    self.s.events.append(dict(t=t,event='booster_contact_cutoff',side=side,pad_heights=pad_heights,
                                              at_target=bool(at_target),distance_to_pad=distance,
                                              tangential_speed=float(np.linalg.norm(vt))))
            else:self.contact_since[side]=None
            if self.cutoff[side] is not None:
                power=0.
                commands[:]=[(g,k) for g,k in commands if g not in self.s.wheels[side]]
            for g in self.s.engines[side]:
                sliders.append((g,'fthrust',power))
                if power>.005:commands.append((g,'ThrustKey'))
            self.previous[side]=(v,up,len(self.s.engines[side])*power)
            report[str(side)]=dict(distance_to_pad=distance,height_above_launch_contact=above_pad,
                                   radial_speed=vr,tangential_speed=float(np.linalg.norm(vt)),
                                   common_target_height=common_height,power=power,
                                   cruise_height=self.cruise_height,max_lateral_speed=self.max_lateral_speed,
                                   braking_speed_limit=braking_speed_limit,
                                   synchronized_terminal=bool(terminal),
                                   final_descent_started=self.final_descent_started,
                                   at_target=bool(at_target),
                                   capturing=bool(self.capturing),
                                   acceleration_per_power=self.acceleration_per_power[side],
                                   fine_attitude=self.fine_attitude[side],wheel_feedback=motor_report,
                                   attitude_error_body=local_error.tolist(),attitude_trim=self.attitude_integral[side].tolist(),
                                   pad_heights=pad_heights,contact_cutoff_at=self.cutoff[side],
                                   upright_cosine=float(up@rad),angular_speed=float(np.linalg.norm(omega)),
                                   touchdown_candidate=bool(grounded))
        return report
