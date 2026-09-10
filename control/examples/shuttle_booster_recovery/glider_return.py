"""Measured blade-servo control for the dry orbiter; gains require flight trials."""
import numpy as np
import json, os
from controller_sdk.protocol import ENV_RUN_DIR
from pathlib import Path
from scipy.spatial.transform import Rotation as R

def unit(x):
    return x/max(np.linalg.norm(x),1e-9)

class GliderReturn:
    def __init__(self,staging):
        self.s=staging
        self.surfaces={'right':('elevonR','bladeR0'), 'left':('elevonL','bladeL0')}
        if 'rudder' in staging.hw['parts']:
            self.surfaces['yaw']=('rudder','tailBlade')
        self.folded='foldR' in staging.hw['parts']
        self.signs={}
        self.cal_start=None
        self.cal_angles=None
        self.cal_failed=False
        self.pitch_integral=0.
        self.route_normal=None
        self.last_bank=0.
        self.previous_guidance_time=None
        # Equal native axial-drag coefficients for the main and small blades.
        # First-order area correction; loaded lift/trim still needs flight data.
        self.lift_area_factor=sum(a['type'] in ('Propeller','Small Propeller')
                                  for a in staging.hw['order'])/4.
        from blade_servo_control import BladeServoControl
        self.servo_control=BladeServoControl()
        calibration_path=(Path(os.environ[ENV_RUN_DIR])/'servo_calibration.json')
        if calibration_path.exists():
            calibration=json.loads(calibration_path.read_text())
            measured=calibration['surfaces']
            valid=all(name in measured and measured[name]['servo_guid']==self.s.guid(servo) and
                      measured[name]['blade_guid']==self.s.guid(blade) and
                      measured[name]['base_guid']==self.s.guid('elevonBase'+('R' if name=='right' else 'L'))
                      for name,(servo,blade) in self.surfaces.items())
            if valid:
                self.signs={name:measured[name]['left_key_sign'] for name in self.surfaces}
                self.s.events.append(dict(t=0.,event='verified_ground_servo_calibration_loaded',source=calibration['source_run'],signs=self.signs))

    def update(self,t,d,coast,released,sliders,commands):
        dt=.04 if self.previous_guidance_time is None else float(np.clip(t-self.previous_guidance_time,.001,.2))
        self.previous_guidance_time=t
        root=self.s.cores[0]
        actual=R.from_quat(d[root]['rotation'])*self.s.saved[root].inv()
        angles={}
        for name,(servo,blade) in self.surfaces.items():
            g=self.s.guid(servo);b=self.s.guid(blade)
            # Remove the moving wing frame, rather than misreading deployment
            # as elevon deflection. Braces keep this foot fixed to its wing.
            frame=actual
            if self.folded:
                foot=self.s.guid('elevonBase'+('R' if name=='right' else 'L'))
                frame=R.from_quat(d[foot]['rotation'])*self.s.saved[foot].inv()
            delta=frame.inv()*R.from_quat(d[b]['rotation'])*self.s.saved[b].inv()
            axis=self.s.saved[g].apply([0,0,1])
            angles[name]=float(delta.as_rotvec()@axis)
            sliders.extend([(g,'rotation-speed',1.),(g,'tension',1.5)])
        ready=not self.folded or bool(self.s.deployment and self.s.deployment.reported)
        if not ready:return dict(calibrated=False,waiting_for_deployment=True,angles=angles)
        height=np.linalg.norm(np.array(d[root]['position'])-[0,-600,0])-600
        if not self.signs and (height<520 or self.cal_failed):
            return dict(calibrated=False,calibration_failed=self.cal_failed,angles=angles)
        if coast and not self.signs and self.cal_start is None:
            self.cal_start=t
            self.cal_angles=angles.copy()
        if self.cal_start is None and not self.signs:return dict(calibrated=False)
        elapsed=t-self.cal_start if self.cal_start is not None else 1.
        if elapsed<.4:
            commands.extend((self.s.guid(a),'leftKey') for a,b in self.surfaces.values())
            return dict(calibrated=False,angles=angles)
        if not self.signs:
            deltas={name:angles[name]-self.cal_angles[name] for name in angles}
            if all(abs(x)>.012 for x in deltas.values()):
                self.signs={name:float(np.sign(x)) for name,x in deltas.items()}
                self.s.events.append(dict(t=t,event='blade_servo_sign_calibration',signs=self.signs,deltas=deltas))
            else:
                # Never accept a later impact or airload as evidence of the
                # sign of a pulse that ended seconds earlier.
                if elapsed>.8:
                    self.cal_failed=True
                    self.s.events.append(dict(t=t,event='blade_servo_calibration_failed',deltas=deltas))
                return dict(calibrated=False,calibration_deltas=deltas)
        goals={name:0. for name in angles}
        diag={}
        if released:
            p=np.array(d[root]['position']);v=np.array(d[root]['velocity'])
            rad=unit(p-[0,-600,0]);h=np.linalg.norm(p-[0,-600,0])-600
            vr=float(v@rad);tangent=v-vr*rad
            if h<500 and np.linalg.norm(v)>5:
                # A shallow glide transitions toward a raised nose for flare.
                # This is aerodynamic control only: all outputs are blade servos.
                gamma=np.arctan2(vr,np.linalg.norm(tangent))
                # return16 held ~10deg while losing orbital support and hit
                # the ground at vr=-37. Request lift before the sink grows.
                # Native AxialDrag scales with density squared and capped v^3.
                # Prefab inspection gives velocityCap=30 (not the class's100
                # default). Above that cap lift is linear in airspeed. The
                # coefficient remains an empirical estimate from return16.
                density=float(np.clip((np.exp(-h/500.)-np.exp(-1.))/(1.-np.exp(-1.)),0.,1.))
                speed=float(np.linalg.norm(v)); vt=float(np.linalg.norm(tangent))
                target_vr=-float(np.clip(.08*max(0.,h-1.),.6,3.))
                lift_required=max(0.,min(22.,8456800./(h+600.)**2)-vt*vt/(h+600.)+.4*(target_vr-vr))
                lift_scale=.0011*self.lift_area_factor*density*density*speed*min(speed*speed,900.)
                lift_aoa=np.arcsin(np.clip(lift_required/max(lift_scale,.01),0.,np.sin(np.deg2rad(40))))
                blend=float(np.clip((400.-h)/60.,0.,1.))
                aoa=(1.-blend)*np.deg2rad(10)+blend*np.clip(lift_aoa,np.deg2rad(8),np.deg2rad(40))
                pitch=np.clip(gamma+aoa,np.deg2rad(-15),np.deg2rad(40))
                nose=unit(tangent)*np.cos(pitch)+rad*np.sin(pitch)
                from field_guidance import field_bank
                if self.route_normal is None:
                    self.route_normal=unit(np.cross(rad,self.s.launch))
                    if self.route_normal@np.cross(rad,v)<0:self.route_normal=-self.route_normal
                bank,field_diag=field_bank(rad,v,self.s.launch,h,self.route_normal)
                bank=float(np.clip(bank,self.last_bank-dt*.12,self.last_bank+dt*.12))
                self.last_bank=bank
                dorsal=rad*np.cos(bank)+unit(np.cross(unit(tangent),rad))*np.sin(bank)
                x=unit(np.cross(nose,dorsal));z=np.cross(x,nose)
                desired=R.from_matrix(np.column_stack([x,nose,z]))
                error=actual.inv().apply((desired*actual.inv()).as_rotvec())
                omega=actual.inv().apply(d[root]['angular_velocity'])
                # return19 retained~10deg pitch error with unsaturated PD
                # output. Integral trim removes that persistent load error.
                candidate=float(np.clip(self.pitch_integral+.08*error[0]*dt,-.4,.4))
                effort=.9*error[0]-2.5*omega[0]+candidate
                if abs(effort)<.65 or error[0]*effort<0:
                    self.pitch_integral=candidate
                pitch_control=np.clip(.9*error[0]-2.5*omega[0]+self.pitch_integral,-.65,.65)
                roll_control=np.clip(.7*error[1]-2.*omega[1],-.20,.20)
                yaw_control=np.clip(1.2*error[2]-1.6*omega[2],-.2,.2)
                goals={'right':float(np.clip(-pitch_control-roll_control,-.75,.75)),
                       'left':float(np.clip(pitch_control-roll_control,-.75,.75)),
                       'yaw':float(-yaw_control)}
                diag.update(attitude_error_deg=np.degrees(error).tolist(),altitude=float(h),radial_speed=vr,
                            belly_down_cosine=float(actual.apply([0,0,1])@rad),**field_diag,
                            target_radial_speed=target_vr,target_aoa_deg=float(np.degrees(aoa)),
                            lift_required=lift_required,lift_scale=lift_scale,lift_area_factor=self.lift_area_factor,
                            pitch_integral=self.pitch_integral)
        diag.update(self.servo_control.update(t,angles,goals,self.surfaces,self.signs,self.s.guid,sliders,commands))
        return dict(calibrated=True,angles=angles,goals=goals,**diag)
