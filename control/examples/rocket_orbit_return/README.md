# Rocket orbit and return: a control tutorial

## One-command run

After [one-command setup](../../../README.md#one-command-setup), run this from the repository root in PowerShell:

```powershell
uv run python control/examples/rocket_orbit_return/run.py
```

This command rebuilds the machine from `machine.json`, launches Besiege, and runs the complete automatic controller. Generated machines, logs, and telemetry are saved under `datacache/manual_cases/`.

Append `--edit-before-start` to place cameras before the mission, or `--prepare-only` to rebuild without starting the simulation. See the [root README](../../../README.md#run-three-automatic-machine-examples) for camera and recording instructions.

## Overview

This example flies a reusable launcher on **LONE ORB**: climb, complete a lap around the planet, remove horizontal velocity, and land. It illustrates a hybrid controller: a discrete mission state machine selects references for continuous feedback laws, which are finally converted into discrete actuator commands.

The explanation follows the executable code in `controller.py`, especially `Frame`, `MissionController`, `AttitudeController`, and `ThrottlePwm`. Distances, accelerations, and fitted gains are in simulation units; they should not be transferred to a real vehicle without a new model.

## Runtime and controller

`run.py` delegates to `control/run_example.py`, which rebuilds `machine.json`, applies `mission.py`, and starts full telemetry at **10 Hz**. The final preset selects `HEAVY_LAUNCHER_POLICY=mission` and requires successful completion. The controller discovers the root, boosters, and two reaction wheels from the prepared machine; wheel axes are bound using their initial offsets relative to the root.

## Mission stages

The altitude target is 100 above the spherical surface. Let `h_agl` denote root altitude minus its initial ground clearance, rather than the height of the lowest collider.

| Stage | Control objective | Transition in `MissionController.decide` |
| --- | --- | --- |
| `pad` | Record ground clearance and choose an initial horizontal direction. Engines remain off. | At simulation time 0.4 s or later, with the configured gravity model available. |
| `guided_ascent` | Build altitude and tangential speed using the guidance laws below. | Any of three insertion tests: acceptable apsides, locally circular speed with low radial speed, or a settled powered-flight energy state. |
| `orbit_coast` | Continue the same feedback guidance while accumulating angular travel. **This stage may fire engines.** | Absolute accumulated sweep reaches 360 degrees. |
| `kill_burn` | Support the vehicle vertically while tilting against horizontal velocity. | Tangential speed falls below 8, or `h_agl` falls below 6. |
| `final_descent` | Track a height-dependent sink speed using adaptive throttle and limited tilt. | `h_agl <= 0.6` and absolute radial speed is at most 3. |
| `touchdown` | Switch engines off and observe settling. | Total speed stays at or below 0.5 for 0.5 s. |

The apsis insertion test requires periapsis altitude at least 55 and apoapsis altitude between 75 and 250. The circular-speed alternative requires tangential speed at least the local circular speed and absolute radial speed below 4. The powered alternative requires tangential speed above 60, specific-energy error below 1200, and absolute radial speed below 6. These are engineering acceptance thresholds, not equivalent definitions of an unpowered orbit.

Sweep is obtained by projecting position into the orbital plane established at insertion, evaluating `atan2`, and unwrapping successive angle differences. It measures travel after insertion, not time spent in flight.

## 1. State and gravity model

The root provides position, velocity, quaternion orientation, and angular velocity. Quaternions use Unity's `(x, y, z, w)` order. Body `+Y` is the rocket's thrust axis. With planet center **c**, radius **r**, and outward radial unit vector **n**:

$$
\mathbf r=\mathbf p-\mathbf c,\quad r=\|\mathbf r\|,\quad
\mathbf n=\mathbf r/r,\quad
v_r=\mathbf v\cdot\mathbf n,\quad
\mathbf v_t=\mathbf v-v_r\mathbf n.
$$

`Frame.from_telemetry` uses explicit scene constants:

$$
\mathbf c=(0,-600,0),\quad R=600,\quad \mu=8{,}456{,}800,
\qquad \mathbf g=-\min(22,\mu/r^2)\mathbf n.
$$

Gravity is constructed from these constants; it is not a live gravity-field measurement. `update_gm` initializes an effective gravitational parameter with `g*r*r`, then smooths updates only outside the gravity clamp (`g < 21.9`). Thus the value logged as `gm_estimate` is derived from the configured model, and its initial value can be biased by the clamp.

The orbital diagnostics use two-body energy and angular momentum:

$$
\epsilon=\frac{\|\mathbf v\|^2}{2}-\frac{\mu}{r},\quad
\ell=\|\mathbf r\times\mathbf v\|,\quad
e=\sqrt{\max(0,1+2\epsilon\ell^2/\mu^2)},
$$

$$
r_p=\frac{\ell^2/\mu}{1+e},\qquad
r_a=\frac{\ell^2/\mu}{1-e},\qquad v_c(r)=\sqrt{\mu/r}.
$$

The code reports an infinite apoapsis for an effectively unbound trajectory. Under thrust and drag, these are instantaneous Keplerian estimates. They do not predict a complete future trajectory, especially near the gravity clamp.

## 2. Guidance: velocity feedback, then energy feedback

At tangential speed at or below 60, `guidance` builds a desired thrust acceleration from radial and tangential components. The radial-speed reference is

$$
v_r^*=\operatorname{clip}(0.25(r_t-r),-8,12).
$$

Allowing a negative reference is essential: after overshooting the target radius, the controller must request descent. The radial demand is approximately

$$
a_r=g\,\gamma\left[1-\min(1,v_t/v_c(r))^2\right]
 +\operatorname{clip}(2.5(v_r^*-v_r),-25,25),
$$

followed by a lower bound of -15. Here `gamma` is the code's gravity-compensation fade: it decreases when apoapsis or climb speed is already too high. The factor involving circular speed reflects the radial equation `r_ddot = a_r - g + v_t^2/r`: orbital motion supplies increasing radial support as tangential speed grows.

The tangential demand is

$$
a_t=\operatorname{clip}(10+4(v_c(r_t)-v_t),0,36).
$$

The constant 10 is empirical drag feedforward. Proportional feedback alone needs a persistent velocity error to produce thrust against drag; feedforward reduces that error. The vector `a_r*n + a_t*east` defines the desired attitude, limited to a 10-degree/s reference slew. Engine duty is the acceleration demand projected onto the **actual** thrust axis, divided by 36 and clipped to `[0,1]`.

Above tangential speed 60, aerodynamic alignment is assumed to dominate the weak wheels. Guidance points along velocity and controls specific energy instead:

$$
\epsilon_t=-\frac{\mu}{2r_t},\qquad
d=\operatorname{clip}\left(0.30+0.6\frac{\epsilon_t-\epsilon}{1000}-0.03v_r,0,1\right).
$$

Duty is then multiplied by the nonnegative cosine of thrust-axis misalignment. Energy feedback accounts for both kinetic and potential energy; the radial-speed term reduces power during a climb and increases it during a sink. This is why `orbit_coast` is a powered maintenance stage in the draggy scene.

## 3. Attitude feedback and binary actuation

For actual thrust direction **u**, desired direction **d**, and world angular velocity **omega**, the attitude loop predicts a short distance ahead:

$$
\mathbf u_p=\operatorname{unit}(\mathbf u+0.35\,\boldsymbol\omega\times\mathbf u),\qquad
\mathbf s=0.55(\mathbf u_p\times\mathbf d)-2.2\boldsymbol\omega.
$$

The cross product supplies a pointing-error direction; the angular-rate term damps overshoot. Each wheel receives the projection of **s** onto its calibrated world torque axis. Values above 0.04 trigger `LeftKey`, below -0.04 trigger `RightKey`, and values inside that deadband release the wheel. This is a PD-like pointing law with bang-bang outputs and first-order lookahead, not a continuous torque servo. It controls the long axis rather than specifying a full three-axis orientation; the cross-product error also vanishes at exact antiparallel alignment.

`ThrottlePwm` converts continuous duty to full engine pulses with an accumulator: add duty every tick, fire if the sum reaches one, and subtract one after firing. Over many ticks the firing fraction approximates the requested duty. This pulse-density/Bresenham scheme differs from changing thrust strength. The runtime pins each booster's `fthrust` to its published maximum and wheel `speed` to its published default; the acceleration divisor remains 36. That retained calibration is an approximation, particularly as fuel mass changes.

## 4. Braking and adaptive landing

`kill_burn` requests upward acceleration `clip(g - 2*v_r, 8, 36)`. Its opposing horizontal acceleration is limited by horizontal speed, a **30-degree** tilt bound, and the remaining acceleration budget `sqrt(36^2 - a_up^2)`. This is a hover-braking law; the current implementation does not use a stopping-distance ignition calculation, despite older configuration fields such as `burn_margin`.

During final descent, the sink reference is

$$
v_r^*=-\max(1.5,\min(25,0.22h_{agl})).
$$

With `e_v = v_r* - v_r`, the throttle loop is

$$
I_{k+1}=\operatorname{clip}(I_k+0.06e_v\Delta t,0,1),\qquad
d=\operatorname{clip}(I_{k+1}+0.05e_v,0,1).
$$

This clamped PI law learns the power needed to support the lighter returning vehicle. Above clearance 8, horizontal speed above 2 may request up to 10 degrees of braking tilt; closer to the ground the attitude reference becomes vertical. The final reference slew is limited to 12 degrees/s.

## Evidence and limitations

`steps.jsonl` records stage, state, duty, actual engine pulses, attitude diagnostics, fuel, and integrity. `mission_summary.json` records transitions, settling, and abort reasons. Block loss, fuel exhaustion, excessive altitude, unexpected ground contact, and duration limits can end the mission; a terminal `done` flag alone is insufficient without checking `abort_reason`.

The optional `CalibratePolicy` uses airborne engine and wheel pulses to estimate specific thrust and wheel acceleration. It is separate from the final mission preset. The model remains a root-state approximation with empirical drag compensation, fixed effective thrust acceleration, and only two calibrated torque axes. A useful study is to compare energy error, radial speed, and duty around the speed-60 switch, then compare requested duty with engine pulses and the landing integrator's adaptation. This reveals the interaction between guidance, actuator limits, and fuel-dependent dynamics.
