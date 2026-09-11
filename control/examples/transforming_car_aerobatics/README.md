# Transforming car aerobatics: trajectory tracking and control allocation

## One-command run

After [one-command setup](../../../README.md#one-command-setup), run this from the repository root in PowerShell:

```powershell
uv run python control/examples/transforming_car_aerobatics/run.py
```

This command rebuilds the machine from `machine.json`, launches Besiege, and runs the complete automatic controller. Generated machines, logs, and telemetry are saved under `datacache/manual_cases/`.

Append `--edit-before-start` to place cameras before the mission, or `--prepare-only` to rebuild without starting the simulation. See the [root README](../../../README.md#run-three-automatic-machine-examples) for camera and recording instructions.

## Overview

This example combines ground driving, mechanical transformation, powered flight, a rolling helical maneuver, and landing. Its educational core is the separation between **trajectory generation**, **translation and attitude feedback**, and **bounded actuator allocation**. A phase machine changes the references and constraints as the vehicle changes configuration.

The main sources are `controller.py`, the mathematical helpers in `hybrid_control.py`, the calibration in `runtime_config.py`, and collider support calculations in `clearance.py`.

## Active mission

The shared runner rebuilds `machine.json` and uses **BARREN EXPANSE**, full telemetry at **25 Hz**, and the environment preset in `mission.py`. Although the function signature is `main(scenario='bank')`, the executable entry point calls **`main("tactical")`**. This selects a 130-unit cruise height, two helix turns, a steep descent, and a rolling landing. The preset enables smooth references. Its `NIGHTBLADE_FLARE_HEIGHT=7.0` applies to the alternative `mission` glide; the tactical branch uses a speed-dependent trigger instead.

## Phase orchestration

Height in the phase logic is normally root height above its initial position. Collider clearance is a separate measurement in world coordinates.

| Default tactical phase | Purpose and exit condition |
| --- | --- |
| `settle` | Calibrate front steering direction with a short hinge pulse, then center steering. Leave after 4 s. |
| `full_wheel_run` | Apply full wheel power for 3 s. |
| `rolling_launch` | Continue driving while adding flight control. Leave above height 8. |
| `climb` | Track cruise height 130 while changing heading and shifting the flight corridor. Leave within 1 height unit and with vertical speed below 1 in magnitude. |
| `cruise` | Stabilize before aerobatics. Enter the helix after 0.5 s when absolute world-Z velocity is below 1. |
| `spiral` | Track a two-turn helical path with a ramped angular speed. Leave at the analytic path duration. |
| `dive_align` | Align the nose with the landing heading. Require 1.5 s, heading error below 0.12 rad, and absolute world-Y angular rate below 0.3. |
| `tactical_dive` | Request forward speed 25 and vertical speed -36, with a 55-degree pitch reference. Leave at the dynamic flare height. |
| `flare` | Rotate toward a nose-up attitude and use lift thrust to arrest descent and forward motion. Leave after 0.7 s once speed along the landing heading is below 12. |
| `level_after_flare` | Blend back toward a supporting attitude and aim for forward speed 10. After 1 s, require horizontal speed below 12 and body-up vertical cosine above 0.85. |
| `landing_commit` | Retract mechanisms and descend toward wheel contact while preserving forward motion. Require qualifying wheel proximity for at least 0.04 s. |
| `drive_after_landing` | Apply wheel power and a small steering excursion for 6 s. |
| `final_brake` | Remove drive power. Complete after 2 s when total speed is below 0.5, provided displacement from the landing origin is at least 8. |

The rolling-contact predicate requires wheel clearance below 0.12, vertical velocity between -1.2 and 0.3, body-forward speed between 4 and 16, absolute sideways speed below 2.5, and body-up vertical cosine above 0.94. It is a geometric/kinematic predicate, not a contact-force sensor.

Other scenarios are retained in the source. `bank` runs `bank_sweep -> recover`; `spiral` performs one helix then `spiral_recover`. The alternative `mission` continues from one helix into `large_turn -> glide -> flare -> level_after_flare -> vertical_land -> stow -> touchdown -> landed_settle -> drive_after_landing -> final_brake`. Its large turn is a half-circle of radius 70 at speed 25. `Join` supplies a quintic position/velocity/acceleration connection and `spiral_entry` has a handler, but the current entry logic transitions directly to `spiral` and does not construct that join.

## 1. A calibrated acceleration model

World `+Y` is up. In the body frame, fans and lift rockets act along `+Y`; axial rockets act along `+Z`. The root's quaternion transforms between frames. Live actuator positions are transformed back to the body frame every sample, so allocation accounts for changing lever arms.

The retained calibration describes translation as

$$
\dot{\mathbf v}\approx k_f\sum_i u_i\,R\mathbf e_y-g\mathbf e_y
 -R\operatorname{diag}(d_x,d_y,d_z)R^T\mathbf v,
$$

with additional lift/axial rocket acceleration coefficients used by the allocator. Here `R` maps body vectors into world coordinates; `u_i` is actuator power. The fitted gravity is approximately **34.288**, and each fan contributes approximately **1.857** acceleration units per power setting. The 12 fans at power 2 produce a fitted total acceleration of about 44.557, only about 1.30 times gravity.

The runtime guidance simplifies the fitted body-dependent drag to the world-vector feedforward **`0.3*v`**. It does not evaluate the full fitted drag matrix each tick. Variables called `force` and `force_body` are specific-force/acceleration demands, not forces in newtons. The fitted coefficients and assumed inverse inertia are specimen-specific; the stored fit residuals come from the same episode used for fitting, not independent validation.

## 2. Translation feedback and analytic aerobatic references

In ordinary flight, height error produces a bounded vertical-speed reference. Velocity tracking then uses a clamped integral and proportional feedback:

$$
\mathbf I_v\leftarrow\operatorname{clip}(\mathbf I_v+0.5(\mathbf v^*-\mathbf v)\Delta t),
\qquad
\mathbf a_c=\operatorname{clip}(1.3(\mathbf v^*-\mathbf v)+\mathbf I_v+\mathbf a_{ff}),
$$

$$
\mathbf f^*=\mathbf a_c+g\mathbf e_y+0.3\mathbf v.
$$

The clips are componentwise and phase-dependent; several aggressive phases freeze integration, and transitions reset it where appropriate. Gravity and drag feedforward supply a baseline demand, leaving feedback to correct tracking and model errors. `attitude(force, forward)` chooses body-up along the force demand and constructs orthogonal right/forward axes with cross products. Tilting lift is therefore how ordinary flight generates lateral acceleration.

`Helix.sample` defines a path in its own frame:

$$
\mathbf p^*(t)=\mathbf p_0+
\begin{bmatrix}r\sin\theta\\r(1-\cos\theta)\\v_f t\end{bmatrix},\qquad
\omega_0=\sqrt{ng/r}.
$$

The current values are `r=30`, `v_f=20`, and `n=1.8`. The path frame is rotated 90 degrees about world Y and translated to the actual entry position. During a 2.5 s ramp, angular speed follows the quintic smoothstep `S(s)=10s^3-15s^4+6s^5`, with `s=t/2.5`; integrating this speed produces the code's angle polynomial. After the ramp, speed is constant. Position, velocity, and acceleration are calculated analytically, including tangential acceleration during the ramp.

Tactical helix tracking uses

$$
\mathbf f^*=\mathbf a^*+g\mathbf e_y+0.3\mathbf v+
\operatorname{clip}(0.8(\mathbf p^*-\mathbf p)+1.5(\mathbf v^*-\mathbf v),-18,18).
$$

At constant angular speed, the reference's transverse centripetal acceleration is `r*omega_0^2 = n*g`. The changing supporting-force direction generates the rolling attitude. This `n` is a trajectory acceleration parameter, not a claim that actual aerodynamic load remains constant throughout the maneuver. Nominal attitude is evaluated 0.10 s ahead; nearby quaternion samples yield angular-rate and angular-acceleration feedforward. A bounded attitude correction adds tracking feedback without completely replacing the nominal maneuver.

## 3. Attitude control on rotations

The controller avoids subtracting Euler angles for full rotations. It computes the shortest rotation vector

$$
\mathbf e_R=\operatorname{rotvec}(R_dR^{-1}),\qquad
\boldsymbol\alpha_b=R^{-1}\left[K_p\mathbf e_R+K_d(\boldsymbol\omega_d-\boldsymbol\omega)
 +\mathbf I_R+\boldsymbol\alpha_d\right].
$$

The world-frame attitude integral accumulates `0.6*e_R*dt` and is clipped to `[-2,2]`. Gains `(Kp,Kd)` are `(20,7)` for the helix, `(12,7)` for flare/leveling, and `(12,5)` otherwise. The output is a body angular-acceleration demand.

For ordinary smooth flight, `AttitudeGovernor` evolves a reference rotation using `alpha_ref = 36*rotation_error - 12*omega_ref`, bounded to acceleration norm 5 and rate norm 1.5. Integration uses substeps at most 0.02 s. The controller uses 0.4 times this bounded acceleration as feedforward. This limits reference jumps and the noise amplification that would result from repeatedly differentiating feedback targets.

The helix and flare bypass the governor. The flare and initial leveling use quaternion rotation-vector interpolation with the same quintic smoothstep, giving analytic rate and acceleration references. Flare interpolation takes 1.2 s toward -40 degrees of pitch; leveling takes 1 s. A separate reaction wheel handles body-Y yaw with a small deadband and a speed slider proportional to effort.

## 4. Bounded least-squares actuator allocation

`allocate` maps individual power settings to four controlled quantities: **body-Y acceleration, body-Z acceleration, body-X angular acceleration, and body-Z angular acceleration**. It does not solve a full six-axis wrench problem. Body-X translation must be achieved by attitude changes, and yaw is controlled separately.

For actuator `i`, its model force vector is **f_i** per unit power and its lever arm is measured relative to the assumed center `[0, 0.45, -0.28]`. The moment follows the rigid-body relation

$$
\boldsymbol\tau_i=\mathbf r_i\times\mathbf f_i.
$$

The allocation column is `[f_iy, f_iz, 0.115*tau_ix, 0.115*tau_iz]`. The factor 0.115 is an approximate inverse-inertia mapping in the acceleration-normalized model. It is not an online identification of the complete inertia tensor.

With these columns forming `A`, SciPy's `lsq_linear` solves

$$
\min_{0\le\mathbf u\le\mathbf u_{max}}
\|W(A\mathbf u-\mathbf b)\|^2+\|\Lambda\mathbf u\|^2.
$$

`W` weights angular tracking by 3 normally and 8 during flare/leveling. Regularization weights are 0.08 for fans and 0.4 for rockets, rising to 1.2 for rockets in conserving phases. This trades tracking residual against actuator use and biases the solution toward renewable fan power; it is not a fuel-optimal trajectory calculation. Power is bounded by 2, and an empty rocket's upper bound is effectively zero. The returned `allocation_residual = A*u-b` exposes demands that cannot be met.

## 5. Landing and mechanical control

For tactical descent, with downward speed `s=max(0,-v_y)`, flare starts below

$$
h_{trigger}=12+0.25s+\frac{s^2}{2\cdot55}.
$$

This combines a height margin, a delay-distance allowance, and the constant-deceleration stopping-distance formula. The value 55 is a chosen effective braking acceleration, not an online measurement. Flare thrust additionally depends on actual pitch and sink speed, rather than attitude reference alone. During leveling, the allocator's requested lift is adjusted to preserve vertical support when available axial thrust cannot cancel horizontal lift.

Front steering uses a kinematic Ackermann construction with wheelbase 6 and half-track 3: `radius=6/tan(abs(steer))`, then inner/outer wheel angles use `atan2(6, radius +/- 3)`. A bicycle-model lateral-acceleration bound reduces steering angle as forward speed rises, approximately enforcing `v_forward^2*tan(steer)/6 <= 4`. Hinge keys close the measured wheel-angle error outside a deadband; initial pulses identify the sign of each steering response.

Pistons and folding hinges follow the phase schedule. Fold angles are measured relative to the body using blade orientation, with goals 0 degrees deployed and 78 degrees stowed. The steering/fold loops use actual geometry, rather than assuming that issuing a key guarantees motion.

`Clearance.minimum` transforms collider support points through each block's live pose and finds the lowest world-Y coordinate. Boxes use corners; spheres and capsules include radii. The model assumes the relevant ground plane is world Y=0. Unsupported mesh geometry is listed as unresolved, and internal moving child colliders use rest transforms; those cases cannot certify a close flare or arbitrary terrain clearance.

## Evidence and study questions

`hybrid_envelope.json` contains phase events, actual and desired states, actuator powers, allocation residuals, mechanical angles, clearance, timing, and completion/failure data. Guards reject block loss, excessive structural displacement, flight-envelope violations, premature flare contact, or large aerobatic tracking error. During the helix, limits include position error 25, attitude error 75 degrees, and height at least 12.

Inspect position error together with `allocation_residual`: increasing gains cannot create missing actuator authority. Compare flare reference rate with measured rate to understand why smooth references matter. Finally, compare commanded hinge motion with measured angles and collider clearance: rigid-body flight tracking and articulated landing geometry are different parts of the same hybrid-control problem.
