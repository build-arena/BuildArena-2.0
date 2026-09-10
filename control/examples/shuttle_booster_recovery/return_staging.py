"""Shared-fuel deorbit and independently observed return characterization.

No mission success assertion: landing acceptance is deliberately separate.
"""
import os
import numpy as np
from scipy.spatial.transform import Rotation as R
from orbital_staging import OrbitalStaging

CENTER = np.array([0., -600., 0.])
GM = 8456800.

def unit(x):
    return x / max(np.linalg.norm(x), 1e-9)

def attitude(nose, dorsal):
    y = unit(nose)
    x = unit(np.cross(y, dorsal))
    z = np.cross(x, y)
    return R.from_matrix(np.column_stack([x, y, z]))

def orbit(p, v):
    rel = p-CENTER
    radius = np.linalg.norm(rel)
    rad = rel/radius
    vr = float(v@rad)
    tangent = v-vr*rad
    momentum = np.cross(rel, v)
    energy = v@v/2-GM/radius
    ecc = np.sqrt(max(0., 1+2*energy*(momentum@momentum)/GM**2))
    peri = (momentum@momentum)/GM/(1+ecc)-600
    return radius, rad, vr, tangent, float(peri)


def tangential_speed_for_periapsis(radius, radial_speed, peri_radius):
    """Energy and angular momentum at a specified periapsis, same current r."""
    if not 0 < peri_radius < radius:
        raise ValueError('Periapsis must be below the current radius')
    return np.sqrt((2*GM*(1/peri_radius-1/radius)+radial_speed**2)/
                   (radius**2/peri_radius**2-1))


def outbound_velocity(radius, radial, tangential_direction, peri_radius, apo_radius):
    """Velocity on the outbound leg of an ellipse with specified apsides."""
    if not 0 < peri_radius <= radius <= apo_radius:
        raise ValueError('Position must lie between the planned apsides')
    momentum=np.sqrt(2*GM*peri_radius*apo_radius/(peri_radius+apo_radius))
    vt=momentum/radius
    speed_squared=2*GM*(1/radius-1/(peri_radius+apo_radius))
    vr=np.sqrt(max(0.,speed_squared-vt*vt))
    return radial*vr+tangential_direction*vt

def predicted_entry_attitude(p, v, entry_radius=1090.):
    """Kepler descending crossing: an inertially fixed entry orientation.

    Tracking the instantaneous orbital frame leaves a dry asymmetrical body
    spinning/precessing during its vacuum coast. Aim at its future entry frame
    and remove angular velocity while booster control is still attached.
    """
    rel=p-CENTER;rad=unit(rel);momentum=np.cross(rel,v);hm=np.linalg.norm(momentum)
    eccentric=np.cross(v,momentum)/GM-rad;e=np.linalg.norm(eccentric)
    if hm<1e-6 or e<1e-6:return None
    cosine=(hm*hm/(GM*entry_radius)-1)/e
    if abs(cosine)>1:return None
    sine=-np.sqrt(max(0.,1-cosine*cosine))
    peri=unit(eccentric);along=np.cross(unit(momentum),peri)
    entry_rad=cosine*peri+sine*along
    entry_v=GM/hm*(-sine*peri+(e+cosine)*along)
    nose=unit(unit(entry_v)*np.cos(.17)+entry_rad*np.sin(.17))
    return attitude(nose,entry_rad)

class ReturnStaging(OrbitalStaging):
    def __init__(self, hw, history, saved):
        super().__init__(hw, history, saved)
        self.phase = 'ascent'
        self.since = 0.
        self.launch = None
        self.entry_target=None
        self.field_window=None
        self.field_window_time=-1e9
        self.entry_stable_since=None
        self.pose_release_trial=os.environ.get('BAILU_POSE_RELEASE_TRIAL')=='1'
        from pose_release_observer import PoseReleaseObserver
        self.pose_release_observer=PoseReleaseObserver(hw)
        self.entry_trim_until=0.
        self.entry_trim_next=0.
        self.entry_trim_commands=[]
        self.control_time=0.
        from momentum_steering import MomentumSteering
        self.entry_steering=MomentumSteering(self)
        self.entry_counterspin=os.environ.get('BAILU_ENTRY_COUNTERSPIN')=='1'
        self.counterspin_steering=MomentumSteering(self)
        self.counterspin_steering.balanced=True
        self.counterspin_steering.balanced_base=.28
        self.counterspin_steering.balanced_delta_limit=.025
        self.counterspin_steering.quiet_after_alignment=os.environ.get('BAILU_ENTRY_QUIET_SPIN')=='1'
        self.counterspin_steering.reduce_paired_spin=os.environ.get('BAILU_ENTRY_PAIRED_SPINDOWN')=='1'
        self.counterspin_steering.settle_hold=os.environ.get('BAILU_ENTRY_SETTLE_HOLD')=='1'
        self.entry_fine=False
        self.steering_diagnostic=None
        self.return_apo_altitude=float(os.environ.get('BAILU_RETURN_APO_ALTITUDE','0'))
        self.entry_aligned_burn=os.environ.get('BAILU_ENTRY_ALIGNED_BURN')=='1'
        self.burn_target=None;self.burn_target_time=None
        self.ascent_engine_trim=np.zeros(3)
        self.burn_engine_points=None
        self.release_probe=os.environ.get('BAILU_DRY_RELEASE_PROBE')=='1'
        self.probe_stable_since=None
        from rigid_rate_observer import RigidRateObserver
        self.rigid_observer=RigidRateObserver(hw)
        self.retro_engines=[self.guid(name) for name in hw['parts'] if name.startswith('retro')]
        self.transverse_engines=[self.guid(name) for name in hw['parts'] if name.startswith('transverse')]
        if self.entry_aligned_burn:
            assert self.return_apo_altitude>0 and len(self.transverse_engines)==4
        self.engines = {s: [hw['guids'][a['id']] for a in hw['order']
                           if a['type']=='Booster' and hw['guids'][a['id']] in self.groups[s]
                           and hw['guids'][a['id']] not in self.retro_engines+self.transverse_engines]
                           for s in [-1, 0, 1]}
        self.all_engines = sum(self.engines.values(), [])+self.retro_engines+self.transverse_engines
        self.all_wheels = sum(self.wheels.values(), [])
        self.rtls = None
        self.transverse_probe=None
        if os.environ.get('BAILU_TRANSVERSE_PROBE')=='1':
            from transverse_probe import TransverseProbe
            self.transverse_probe=TransverseProbe(self)
        self.glider = None
        self.wheel_response = None
        self.momentum_steering=None
        if os.environ.get('BAILU_MOMENTUM_HOLD')=='1':
            from momentum_steering import MomentumSteering
            self.momentum_steering=MomentumSteering(self)
        if os.environ.get('BAILU_WHEEL_RESPONSE')=='1':
            from wheel_response import WheelResponse
            self.wheel_response=WheelResponse(self)
        if os.environ.get('BAILU_RTLS')=='1':
            from booster_return import BoosterReturn
            self.rtls = BoosterReturn(self)
        if os.environ.get('BAILU_GLIDER')=='1':
            from glider_return import GliderReturn
            self.glider = GliderReturn(self)

    def change(self, phase, t, **data):
        self.phase, self.since = phase, t
        self.events.append(dict(t=t, event=phase, **data))

    def steer(self, d, core, desired, wheels, sliders, commands, desired_omega=None):
        self.steering_diagnostic=None
        actual = R.from_quat(d[core]['rotation'])*self.saved[core].inv()
        error = (desired*actual.inv()).as_rotvec()
        omega = np.array(d[core]['angular_velocity'])
        desired_omega = np.zeros(3) if desired_omega is None else desired_omega
        use_fine=self.phase=='entry_align' or (self.entry_aligned_burn and self.phase in ('retrograde_align','deorbit_burn'))
        if use_fine and (self.entry_fine or np.linalg.norm(error)<.35):
            # nonlinear_hold05 met the unchanged release gate in orbital coast.
            # Latch fine control to avoid abrupt motor/brake mode switching.
            self.entry_fine=True
            # return37 root rates contain substantial >4Hz fluctuations.
            # Filter damping feedback only after the burn; release still uses
            # the raw root rate and its unchanged dwell/threshold below.
            steering=self.counterspin_steering if self.phase=='entry_align' and self.entry_counterspin else self.entry_steering
            self.steering_diagnostic=steering.update(self.control_time,d,desired,sliders,commands,desired_omega=desired_omega,
                                                              rate_filter_seconds=.12 if self.phase=='entry_align' else 0.)
            return actual,float(np.linalg.norm(error)),float(np.linalg.norm(omega))
        torque = actual.inv().apply(.8*error-2.4*(omega-desired_omega))
        for g in wheels:
            val = torque@(-self.saved[g].apply([0, 0, 1]))
            sliders.extend([(g, 'speed', .5), (g, 'damper', 2. if self.phase=='entry_align' else .5)])
            if abs(val)>.025:
                commands.append((g, 'LeftKey' if val>0 else 'RightKey'))
        return actual, float(np.linalg.norm(error)), float(np.linalg.norm(omega))

    def update(self, t, d, coast, sliders, commands):
        self.control_time=t
        states = {s: (np.mean([d[g]['position'] for g in gs if g in d], axis=0),
                      np.mean([d[g]['velocity'] for g in gs if g in d], axis=0))
                  for s, gs in self.groups.items()}
        p = np.mean([x['position'] for x in d.values()], axis=0)
        v = np.mean([x['velocity'] for x in d.values()], axis=0)
        radius, rad, vr, tangent, peri = orbit(p, v)
        orbital_omega = np.cross(p-CENTER,v)/(radius*radius)
        if self.launch is None:
            self.launch = unit(np.array(d[self.cores[0]]['position'])-CENTER)
        if self.rtls:
            self.rtls.observe_launch(d)
        if coast and self.separated_at is None:
            if self.previous is not None:
                self.sweep += np.arctan2(np.linalg.norm(np.cross(self.previous, rad)), self.previous@rad)
            self.previous = rad
            if self.phase=='ascent':
                self.change('orbit_coast', t)
            # A periapsis-side burn left only ~12s to turn before atmosphere
            # in flyingwing_return01. Wait for a high, near-apogee window so
            # release, deployment and actuator calibration finish in vacuum.
            if not self.release_probe and self.phase=='orbit_coast' and self.sweep>=2*np.pi and radius>1400:
                from field_guidance import burn_window
                if t-self.field_window_time>.25:
                    self.field_window=burn_window(p,v,self.launch,self.return_apo_altitude+600)
                    self.field_window_time=t
                if self.field_window and abs(self.field_window['phase_error'])<.018:
                    self.change('retrograde_align', t, sweep_degrees=float(np.degrees(self.sweep)),field_window=self.field_window)

        diag = {}
        if self.field_window:diag['field_window']=self.field_window
        if self.phase in ('retrograde_align', 'deorbit_burn', 'entry_align', 'return'):
            sliders = [(g, 'fthrust', 0.) for g in self.all_engines]
            commands = []
            if self.phase in ('retrograde_align', 'deorbit_burn'):
                rp = 900.
                vt_goal = tangential_speed_for_periapsis(radius,vr,rp)
                # Brake against the CURRENT tangential velocity. Future entry
                # may lie over90deg around the planet; its attitude cannot
                # determine which direction decreases present orbital energy.
                burn_engines=sum(self.engines.values(), [])
                desired = attitude(-unit(tangent), rad)
                expected_axis=-unit(tangent)
                velocity_error=None
                energy=float(v@v/2-GM/radius)
                apo_altitude=-GM/energy-(peri+600)-600 if energy<0 else float('inf')
                if self.return_apo_altitude>0:
                    # Reshape the completed orbit into an outbound ellipse.
                    # The dry shuttle then has a long ballistic coast while
                    # independently powered boosters return to the pads.
                    target_velocity=outbound_velocity(radius,rad,unit(tangent),rp,self.return_apo_altitude+600)
                    velocity_error=target_velocity-v
                    expected_axis=unit(velocity_error)
                    dorsal=rad if np.linalg.norm(np.cross(expected_axis,rad))>.001 else unit(tangent)
                    desired=attitude(expected_axis,dorsal)
                    diag.update(return_arc_target_apo_altitude=self.return_apo_altitude,
                                return_arc_actual_apo_altitude=apo_altitude,
                                return_arc_velocity_error=float(np.linalg.norm(velocity_error)))
                tracking_omega=orbital_omega
                if self.entry_aligned_burn:
                    desired=predicted_entry_attitude(p,target_velocity)
                    assert desired is not None,'Outbound target has no atmospheric crossing'
                    from entry_aligned_burn import bounded_thrust_pitch
                    pitch_bias=bounded_thrust_pitch(desired.inv().apply(velocity_error))
                    desired=desired*R.from_rotvec([pitch_bias,0.,0.])
                    diag['burn_entry_pitch_offset_deg']=float(np.degrees(pitch_bias))
                    if self.burn_target is not None:
                        desired=R.from_rotvec((desired*self.burn_target.inv()).as_rotvec()*.15)*self.burn_target
                        dt=max(.001,t-self.burn_target_time)
                        tracking_omega=(desired*self.burn_target.inv()).as_rotvec()/dt
                        tracking_omega*=min(1.,.15/max(np.linalg.norm(tracking_omega),1e-9))
                    self.burn_target=desired;self.burn_target_time=t
                actual, err, omega = self.steer(d, self.cores[0], desired, self.all_wheels, sliders, commands, tracking_omega)
                if self.phase=='retrograde_align' and err<.14 and omega<.12:
                    self.change('deorbit_burn', t)
                power = 0.
                mixed=None
                if self.phase=='deorbit_burn':
                    # return12 only skimmed the atmosphere and coasted back
                    # into vacuum with uncorrectable pitch rate. Aim deeper
                    # while retaining the external descending release gate.
                    if self.entry_aligned_burn and err<.14 and omega<.06:
                        from entry_aligned_burn import allocate
                        local_error=actual.inv().apply((desired*actual.inv()).as_rotvec())
                        local_rate=actual.inv().apply(np.array(d[self.cores[0]]['angular_velocity'])-tracking_omega)
                        mixed=allocate(actual.inv().apply(velocity_error),local_error,local_rate,len(burn_engines))
                        power=max(mixed[k] for k in ('axial_power','upper_power','lower_power'))
                    elif not self.entry_aligned_burn and actual.apply([0, 1, 0])@expected_axis>.96:
                        deficit=np.linalg.norm(velocity_error) if velocity_error is not None else np.linalg.norm(tangent)-vt_goal
                        power = float(np.clip(deficit*.7/(3.5*len(burn_engines)), 0, .4))
                        # Side nozzles removed per user design constraint. Rotate
                        # the stack to burn direction and retain calibrated axial
                        # force compensation and pitch trim on main engines only.
                        local_error=actual.inv().apply((desired*actual.inv()).as_rotvec())
                        local_rate=actual.inv().apply(np.array(d[self.cores[0]]['angular_velocity'])-tracking_omega)
                        mixed=dict(axial_power=power,upper_power=0.,lower_power=0.,axial_only=True)
                    orbit_ready=peri<330
                    if self.return_apo_altitude>0:
                        # return42 spent 89% of quiet alignment outside the
                        # unchanged peri<330 release window after stopping at
                        # its boundary. Finish near the intended300 instead.
                        orbit_ready=280<peri<310 and abs(apo_altitude-self.return_apo_altitude)<80 and vr>3
                    if orbit_ready:
                        self.change('entry_align', t, periapsis_altitude=peri,apoapsis_altitude=apo_altitude,
                                    return_arc='outbound' if self.return_apo_altitude>0 else 'direct')
                        power = 0.
                        mixed=None
                if power>.005 and not self.entry_aligned_burn and mixed is None:
                    sliders = [(g, key, power if key=='fthrust' and g in burn_engines else value) for g, key, value in sliders]
                    commands.extend((g, 'ThrustKey') for g in burn_engines)
                if mixed is not None:
                    allocated={g:mixed['axial_power'] for g in burn_engines}
                    # Equal main power disturbed pitch in return35; use the
                    # ascent geometry mixer and last powered pitch trim. Sum
                    # of axial settings is unchanged; each can reach.6.
                    from axial_burn_trim import distribute,request_moments
                    if self.burn_engine_points is None:
                        points=np.array([d[g]['position'] for g in burn_engines])
                        self.burn_engine_points=actual.inv().apply(points-points.mean(0))
                    requested,feedforward=request_moments(local_error,local_rate,self.ascent_engine_trim)
                    powers,trim_diag=distribute(self.burn_engine_points,mixed['axial_power'],requested)
                    allocated.update(zip(burn_engines,powers.tolist()))
                    mixed['axial_trim']=dict(**trim_diag,ascent_trim=self.ascent_engine_trim.tolist(),
                                            used_feedforward_trim=feedforward.tolist(),
                                            engine_powers=dict(zip(burn_engines,powers.tolist())))
                    for name,bid in self.hw['parts'].items():
                        if name.startswith('transverse'):
                            allocated[self.hw['guids'][bid]]=mixed['lower_power' if name.endswith('Lower') else 'upper_power']
                    # Allocator values describe full-force-equivalent thrust.
                    # Real prefab thrust falls sharply with forward velocity.
                    from native_thruster_force import FULL_FORCE_PER_SETTING,force_for_throttle,throttle_for_force
                    native_detail={}
                    for g,equivalent in list(allocated.items()):
                        axis=R.from_quat(d[g]['rotation']).apply([0,0,-1])
                        speed=float(axis@np.asarray(d[g]['velocity']))
                        requested_force=FULL_FORCE_PER_SETTING*equivalent
                        native_power=throttle_for_force(requested_force,speed)
                        predicted_force=force_for_throttle(native_power,speed)
                        allocated[g]=native_power
                        native_detail[g]=dict(equivalent_power=equivalent,native_power=native_power,
                                              axial_velocity=speed,requested_force=requested_force,predicted_force=predicted_force,
                                              saturated=predicted_force+1e-5<requested_force)
                    mixed['native_engine_allocation']=native_detail
                    mixed['native_thrust_compensation']=dict(
                        requested_force_sum=sum(a['requested_force'] for a in native_detail.values()),
                        predicted_force_sum=sum(a['predicted_force'] for a in native_detail.values()),
                        saturated_engines=sum(a['saturated'] for a in native_detail.values()),
                        max_native_setting=max(allocated.values()))
                    power=max(allocated.values())
                    sliders=[(g,k,allocated.get(g,value) if k=='fthrust' else value) for g,k,value in sliders]
                    from entry_aligned_burn import active_engines
                    commands.extend((g,'ThrustKey') for g in active_engines(allocated))
                    diag['mixed_burn']=mixed
                diag.update(attitude_error_deg=float(np.degrees(err)), angular_speed=omega,
                            angular_velocity_body=actual.inv().apply(d[self.cores[0]]['angular_velocity']).tolist(),
                            actual_engine_power=power,
                            deorbit_engine_mode='main_engines_outbound_arc' if self.return_apo_altitude>0 else 'main_engines_current_retrograde',
                            deorbit_engine_guids=burn_engines)
                if self.entry_aligned_burn:
                    diag['deorbit_engine_mode']='entry_aligned_main_and_transverse'
                    diag['tracking_omega']=tracking_omega.tolist()
                    if self.steering_diagnostic is not None:
                        steer_diag=self.steering_diagnostic
                        cap=steer_diag.get('feedback_output_cap')
                        diag['burn_steering']={k:steer_diag[k] for k in ('error_deg','omega','omega_body','bias','feedback_output_cap','feedback_gains') if k in steer_diag}
                        diag['burn_steering']['saturated_motors']=sum(abs(v)>=cap-1e-7 for v in steer_diag.get('applied_motors',{}).values()) if cap is not None else None

            if self.phase=='entry_align':
                # Belly down, nose slightly above flight path before releasing
                # all attitude authority on the dry orbiter.
                desired=predicted_entry_attitude(p,v)
                if desired is None:desired=attitude(unit(v),rad)
                if self.entry_target is None:self.entry_target=desired
                else:self.entry_target=R.from_rotvec((desired*self.entry_target.inv()).as_rotvec()*.15)*self.entry_target
                _, err, omega = self.steer(d, self.cores[0], self.entry_target, self.all_wheels, sliders, commands, np.zeros(3))
                diag.update(attitude_error_deg=float(np.degrees(err)),entry_alignment_mode='future_entry_inertial',angular_speed=omega)
                if self.steering_diagnostic is not None:
                    diag['entry_steering']={k:self.steering_diagnostic[k] for k in ('control_rate_body','rate_filter_seconds','bias','feedback_output_cap','mode','base_speed','differential_speed','quiet_latched_at','rotor_filter_seconds','paired_spin_reduction','settle_hold_started','rotor_abs_mean_rad_s','net_equal_inertia_spin_norm') if k in self.steering_diagnostic}
                # Release in vacuum as soon as entry attitude is stable.
                # return22 had a qualifying interval at h1632, but waiting
                # until h800 delayed booster RTLS by ~35 seconds. Keep the
                # original attitude/rate/dwell and lower-altitude safeguards.
                release_window=1160<radius<2400 and vr<-3
                omega_limit=.012
                upper_altitude=1800.
                if self.return_apo_altitude>0:
                    # Unlike a direct descending return, outbound separation
                    # deliberately precedes apogee. Keep vacuum and bound-entry
                    # checks and tighten spin for the longer unpowered coast.
                    upper_altitude=self.return_apo_altitude+100.
                    release_window=1160<radius<upper_altitude+600 and 0<peri<330 and v@v/2-GM/radius<0
                    omega_limit=.003
                diag['release_window']=bool(release_window)
                diag['release_altitude_limits']=[560.,upper_altitude]
                diag['release_angular_speed_limit']=omega_limit
                spin_ready=omega<omega_limit
                diag['release_gate_method']='raw_root_rate'
                if self.pose_release_trial:
                    observation=self.pose_release_observer.update(t,d)
                    diag['pose_release_observation']=observation
                    diag['original_raw_spin_gate_met']=bool(spin_ready)
                    diag['release_gate_method']='experimental_multiscale_pose'
                    spin_ready=observation['ready']
                if err<.14 and spin_ready and release_window:
                    if self.entry_stable_since is None:self.entry_stable_since=t
                else:self.entry_stable_since=None
                if self.entry_stable_since is not None and t-self.entry_stable_since>.5:
                    self.separated_at = t
                    self.change('return', t, periapsis_altitude=peri)
                    self.events.append(dict(t=t, event='simultaneous_coupler_release', sweep_degrees=float(np.degrees(self.sweep)),
                                            release_gate_method=diag['release_gate_method'],raw_root_rate=omega,
                                            pose_observation=diag.get('pose_release_observation')))

            if self.phase=='return':
                # Reset commands on the release frame too: no orbiter wheels
                # or engines are ever commanded after separation.
                sliders = [(g, 'fthrust', 0.) for g in self.all_engines]
                commands = []
                if t-self.separated_at<.2:
                    for name in ('sepR', 'sepL', 'upperSepR', 'upperSepL'):
                        g = self.guid(name)
                        sliders.append((g, 'epower', 2.))
                        commands.append((g, 'DetachKey'))
                if self.rtls and t-self.separated_at>5:
                    diag['rtls']=self.rtls.update(t,d,sliders,commands)
                else:
                    for s in (-1, 1):
                        core = self.cores[s]
                        actual = R.from_quat(d[core]['rotation'])*self.saved[core].inv()
                        self.steer(d, core, actual, self.wheels[s], sliders, commands)

        if self.deployment:
            clearance=min(np.linalg.norm(states[s][0]-states[0][0]) for s in (-1,1))
            allowed=self.separated_at is not None and t-self.separated_at>5 and clearance>15
            diag['deployment']=self.deployment.update(t,d,allowed,sliders,commands)
        if self.glider:
            diag['glider']=self.glider.update(t,d,coast,self.separated_at is not None,sliders,commands)
        if self.wheel_response and self.phase=='orbit_coast':
            diag['wheel_response']=self.wheel_response.update(t,d,sliders,commands)
        if self.momentum_steering and self.phase=='orbit_coast':
            diag['momentum_hold']=self.momentum_steering.update(t,d,None,sliders,commands)
        if self.transverse_probe and self.phase=='orbit_coast':
            diag['transverse_probe']=self.transverse_probe.update(t,d,sliders,commands)
        if self.release_probe:
            diag['rigid_rate']=self.rigid_observer.update(t,d)
            if self.phase=='orbit_coast' and 'momentum_hold' in diag:
                ready=radius>1160 and diag['rigid_rate']['low_spin_candidate'] and diag['momentum_hold']['error_deg']<np.degrees(.14)
                if ready:
                    if self.probe_stable_since is None:self.probe_stable_since=t
                else:self.probe_stable_since=None
                if self.probe_stable_since is not None and t-self.probe_stable_since>.5:
                    self.separated_at=t
                    self.change('return',t,engineering_probe=True)
                    self.events.append(dict(t=t,event='simultaneous_coupler_release',engineering_probe=True,
                                            sweep_degrees=float(np.degrees(self.sweep)),rigid_rate=diag['rigid_rate']))
                    sliders=[(g,'fthrust',0.) for g in self.all_engines];commands=[]
                    for name in ('sepR','sepL','upperSepR','upperSepL'):
                        g=self.guid(name);sliders.append((g,'epower',2.));commands.append((g,'DetachKey'))
        diag.update(phase=self.phase, sweep_degrees=float(np.degrees(self.sweep)),
                    separated_at=self.separated_at,
                    center_distances={f'{a}:{b}':float(np.linalg.norm(states[a][0]-states[b][0]))
                                      for a,b in [(-1,0),(1,0),(-1,1)]},
                    groups={str(s): dict(altitude=float(np.linalg.norm(p0-CENTER)-600),
                                        speed=float(np.linalg.norm(v0)),
                                        periapsis_altitude=orbit(p0,v0)[4])
                            for s,(p0,v0) in states.items()})
        # A phase can overwrite an engine slider in this update.
        sliders=[(g,k,val) for (g,k),val in {(g,k):val for g,k,val in sliders}.items()]
        return sliders, commands, diag
