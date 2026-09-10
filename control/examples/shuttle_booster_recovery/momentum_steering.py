"""Nonlinear native wheel speed command; no automatic brake.

Historical filename retained for probe compatibility. Identification is
exploratory: closed-loop fits alone do not establish actuator coefficients.
"""
import os
import numpy as np
from scipy.spatial.transform import Rotation as R

class MomentumSteering:
    def __init__(self,s):
        self.s=s;self.last=None;self.motor=np.zeros(3);self.desired=None;self.bias=np.zeros(3)
        self.observed_feedback=os.environ.get('BAILU_OBSERVED_WHEEL_FEEDBACK')=='1'
        self.feedback_kp=float(os.environ.get('BAILU_WHEEL_FEEDBACK_KP','.8'))
        self.feedback_kd=float(os.environ.get('BAILU_WHEEL_FEEDBACK_KD','2.4'))
        self.feedback_ki=float(os.environ.get('BAILU_WHEEL_FEEDBACK_KI','0'))
        self.baseline_cap=float(os.environ.get('BAILU_WHEEL_BASELINE_CAP','inf'))
        self.output_cap=float(os.environ.get('BAILU_WHEEL_OUTPUT_CAP','.5'))
        self.balanced=os.environ.get('BAILU_BALANCED_WHEELS')=='1'
        self.balanced_base=.36;self.balanced_delta_limit=.06
        self.filtered_rate_error_world=None;self.previous_filter_seconds=0.
        self.quiet_after_alignment=False;self.quiet_since=None;self.quiet_latched_at=None
        self.rotor_filter_seconds=0.;self.filtered_rotor_motor={}
        self.reduce_paired_spin=False
        self.settle_hold=False;self.hold_motors=None;self.hold_started=None;self.next_hold=0.

    def update(self,t,d,desired,sliders,commands,desired_omega=None,rate_filter_seconds=0.):
        s=self.s;g=s.cores[0]
        actual=R.from_quat(d[g]['rotation'])*s.saved[g].inv()
        if self.desired is None:self.desired=actual
        if desired is None:desired=self.desired
        dt=.04 if self.last is None else float(np.clip(t-self.last,.001,.2))
        self.last=t
        error=actual.inv().apply((desired*actual.inv()).as_rotvec())
        omega=actual.inv().apply(d[g]['angular_velocity'])
        rate_error_world=np.asarray(d[g]['angular_velocity'])-(np.asarray(desired_omega) if desired_omega is not None else np.zeros(3))
        if rate_filter_seconds>0 and self.filtered_rate_error_world is not None and self.previous_filter_seconds==rate_filter_seconds:
            alpha=1-np.exp(-dt/rate_filter_seconds)
            self.filtered_rate_error_world+=alpha*(rate_error_world-self.filtered_rate_error_world)
        else:self.filtered_rate_error_world=rate_error_world.copy()
        self.previous_filter_seconds=rate_filter_seconds
        control_rate=actual.inv().apply(self.filtered_rate_error_world)
        if self.quiet_after_alignment and self.balanced:
            good=np.linalg.norm(error)<np.radians(1.) and np.linalg.norm(omega)<.03
            if good:
                if self.quiet_since is None:self.quiet_since=t
            else:self.quiet_since=None
            if self.quiet_since is not None and t-self.quiet_since>.5:
                # Opposed high-speed pairs first remove inherited net spin.
                # Then preserve measured momentum below the native 5rad/s cap,
                # rather than continuously exciting the coupled joints.
                self.quiet_latched_at=t;self.balanced=False;self.observed_feedback=True
                self.feedback_kp=.2;self.feedback_kd=1.;self.feedback_ki=0.
                self.baseline_cap=.225;self.output_cap=.235;self.bias[:]=0.
                self.rotor_filter_seconds=.12
        if self.balanced:
            # Optional experiment: run each equal-axis pair in opposite
            # directions, then vary their speeds differentially. return28
            # retained a large net rotor-spin proxy and never held low body
            # rate for the long-coast release. This mode aims to cancel rotor
            # momentum instead of preserving each inherited spin separately.
            wheels=s.all_wheels
            pairs={i:[] for i in range(3)}
            for wheel in wheels:
                axis=-s.saved[wheel].apply([0,0,1]);i=int(np.argmax(abs(axis)))
                assert abs(axis[i])>.999,'Balanced mode requires orthogonal wheel pairs'
                pairs[i].append((wheel,float(np.sign(axis[i]))))
            assert all(len(pair)==2 for pair in pairs.values()),'Balanced mode requires exactly two wheels per axis'
            sliders[:]=[(g,k,v) for g,k,v in sliders if g not in wheels]
            commands[:]=[(g,k) for g,k in commands if g not in wheels]
            base=self.balanced_base
            # cap02 observed ~.093rad/s^2 at pair speed.3 above the native
            # rotor cap. This approximate axis-X slope is only a starting
            # gain; other axes and the coupled controller require live tests.
            acceleration=.2*error-1.*control_rate
            delta=np.clip(acceleration/(2*2.8*base),-self.balanced_delta_limit,self.balanced_delta_limit)
            applied={}
            for i,pair in pairs.items():
                for direction,(wheel,axis_sign) in zip((1.,-1.),pair):
                    value=float(axis_sign*(direction*base+delta[i]))
                    sliders.extend([(wheel,'speed',abs(value)),(wheel,'damper',0.)])
                    commands.append((wheel,'LeftKey' if value>=0 else 'RightKey'))
                    applied[wheel]=value
            return dict(error_deg=float(np.degrees(np.linalg.norm(error))),omega=float(np.linalg.norm(omega)),
                        control_rate_body=control_rate.tolist(),rate_filter_seconds=rate_filter_seconds,
                        omega_body=omega.tolist(),mode='balanced_counterspin_experiment',
                        applied_motors=applied,differential_speed=delta.tolist(),base_speed=base,
                        acceleration_gains=[.2,1.])
        effort=(.35*error-1.2*control_rate)/np.array([1.52,1.95,1.08])
        self.motor=np.sign(effort)*np.sqrt(np.minimum(abs(effort),.49))
        wheels=s.all_wheels
        sliders[:]=[(g,k,v) for g,k,v in sliders if g not in wheels]
        commands[:]=[(g,k) for g,k in commands if g not in wheels]
        applied={}
        trim_delta=np.zeros(3)
        if self.observed_feedback and self.feedback_ki>0 and np.linalg.norm(error)<.25 and np.linalg.norm(omega)<.05:
            trim_delta=np.clip(self.bias+self.feedback_ki*error*dt,-.05,.05)-self.bias
        trim_axes=set()
        observed_rotor_speeds=[];net_rotor_spin=np.zeros(3)
        for g in wheels:
            value=float(self.motor@(-s.saved[g].apply([0,0,1])))
            if self.observed_feedback:
                # Native target at unit speed is1200deg/s. Preserve measured
                # rotor momentum at zero body error instead of commanding
                # stopped wheels and undoing the preceding correction.
                axis=-s.saved[g].apply([0,0,1])
                relative=actual.inv().apply(d[g]['angular_velocity'])-omega
                measured_motor=-float(relative@axis)/np.radians(1200.)
                observed_rotor_speeds.append(abs(float(relative@axis)))
                net_rotor_spin+=float(relative@axis)*axis
                if self.rotor_filter_seconds>0:
                    previous=self.filtered_rotor_motor.get(g,measured_motor)
                    measured_motor=previous+(1-np.exp(-dt/self.rotor_filter_seconds))*(measured_motor-previous)
                    self.filtered_rotor_motor[g]=measured_motor
                # Rotor rates above the native cap can sustain body torque;
                # do not preserve that saturated state as neutral feedback.
                measured_motor=float(np.clip(measured_motor,-self.baseline_cap,self.baseline_cap))
                # Integrate small persistent attitude error once per body axis,
                # with anti-windup at the existing native output limits.
                axis_index=int(np.argmax(abs(axis)))
                correction=float((self.feedback_kp*error-self.feedback_kd*control_rate+self.bias)@axis)
                step=float(trim_delta@axis) if axis_index not in trim_axes else 0.
                candidate=measured_motor+correction+step
                if abs(candidate)<=self.output_cap or candidate*step<0:
                    self.bias+=axis*step
                    correction+=step
                trim_axes.add(axis_index)
                value=float(np.clip(measured_motor+correction,-self.output_cap,self.output_cap))
            # BAA4 keys threshold at .5: fractional key values are NOT analog.
            # Native force scales with speed*targetSpin; square-root speed
            # mapping compensates its small-command loss of authority.
            sliders.extend([(g,'speed',abs(value)),(g,'damper',0.)])
            commands.append((g,'LeftKey' if value>=0 else 'RightKey'))
            applied[g]=value
        before_pair_reduction=None
        if self.reduce_paired_spin and self.quiet_latched_at is not None:
            from paired_spin import reduce_opposed_spin
            before_pair_reduction=dict(applied)
            axes={g:-s.saved[g].apply([0,0,1]) for g in wheels}
            applied=reduce_opposed_spin(applied,axes,dt)
            # Reduction stays in each physical pair's original min/max range,
            # preserving output bounds and its signed sum before actuation.
            sliders[:]=[(g,k,v) for g,k,v in sliders if g not in wheels]
            commands[:]=[(g,k) for g,k in commands if g not in wheels]
            for g,value in applied.items():
                sliders.extend([(g,'speed',abs(value)),(g,'damper',0.)])
                commands.append((g,'LeftKey' if value>=0 else 'RightKey'))
        if self.settle_hold and self.quiet_latched_at is not None:
            unsafe=np.linalg.norm(error)>np.radians(2.) or np.linalg.norm(omega)>.03
            if self.hold_motors is not None and (unsafe or t-self.hold_started>=3.):
                self.hold_motors=None;self.hold_started=None;self.next_hold=t+5.
            if self.hold_motors is None and t>=self.next_hold and t-self.quiet_latched_at>5.:
                if np.linalg.norm(error)<np.radians(1.) and np.linalg.norm(control_rate)<.003 and np.linalg.norm(omega)<.01:
                    # Freeze the already-issued bounded command, not a zero-
                    # speed brake. Release still needs the original raw gate.
                    self.hold_motors=dict(applied);self.hold_started=t
            if self.hold_motors is not None:
                applied=dict(self.hold_motors)
                sliders[:]=[(g,k,v) for g,k,v in sliders if g not in wheels]
                commands[:]=[(g,k) for g,k in commands if g not in wheels]
                for g,value in applied.items():
                    sliders.extend([(g,'speed',abs(value)),(g,'damper',0.)])
                    commands.append((g,'LeftKey' if value>=0 else 'RightKey'))
        return dict(error_deg=float(np.degrees(np.linalg.norm(error))),omega=float(np.linalg.norm(omega)),
                    mode='quiet_observed_feedback' if self.quiet_latched_at is not None else 'momentum_feedback',
                    quiet_latched_at=self.quiet_latched_at,rotor_filter_seconds=self.rotor_filter_seconds,
                    paired_spin_reduction=before_pair_reduction is not None,
                    motors_before_pair_reduction=before_pair_reduction,
                    settle_hold_started=self.hold_started,
                    rotor_abs_mean_rad_s=float(np.mean(observed_rotor_speeds)) if observed_rotor_speeds else None,
                    net_equal_inertia_spin_norm=float(np.linalg.norm(net_rotor_spin)) if observed_rotor_speeds else None,
                    control_rate_body=control_rate.tolist(),rate_filter_seconds=rate_filter_seconds,
                    omega_body=omega.tolist(),motor=self.motor.tolist(),bias=self.bias.tolist(),
                    observed_wheel_feedback=self.observed_feedback,applied_motors=applied,
                    feedback_gains=[self.feedback_kp,self.feedback_kd],feedback_ki=self.feedback_ki,
                    feedback_baseline_cap=self.baseline_cap if np.isfinite(self.baseline_cap) else None,
                    feedback_output_cap=self.output_cap)
