"""Provisional main/side allocation for an entry-facing outbound burn.

Pitch slopes come from matched frozen-wheel pulses02/03. Translational gains
remain approximate; the allocator requires closed-loop flight validation.
"""
import numpy as np

UPPER_PITCH=-1.6054706342126293
LOWER_PITCH=.4765442181769943
NEUTRAL_UPPER=LOWER_PITCH/(LOWER_PITCH-UPPER_PITCH)

def active_engines(powers):
    """Key activation must include small allocated thrust, not impose.005 deadband."""
    return [guid for guid,value in powers.items() if value>1e-8]

def bounded_thrust_pitch(velocity_error_in_entry_frame):
    """Bias within90deg toward main-engine-dominant +Y/-Z correction.

The bound may prevent a feasible solution for very large errors; callers
still inspect the actual local residual. Entry pose is selected after burn completion.
"""
    y,z=velocity_error_in_entry_frame[1:]
    # return43 crossed the old .5 deadband before meeting the interior orbital
    # target. Removing its bias reversed accessible thrust axes and paused
    # correction for a large slew. Direction remains meaningful for small burns;
    # only a numerically zero demand has no preferred thrust direction.
    if np.hypot(y,z)<1e-6:return 0.
    angle=float(np.arctan2(y,-z))
    # return34 kept demand at the10deg cone edge: side engines saturated
    # while axial power stayed small and the outbound velocity error grew.
    # Prefer65deg toward +Y so the nine axial engines share the correction.
    # return37 reached its orbital target but spent153s on the burn: a45deg
    # bound left late demand almost entirely on the small side-engine bank.
    # A90deg bound permits more axial work. Final entry pose is unbiased;
    # the larger post-burn slew and real fuel/time savings need flight tests.
    wanted=np.deg2rad(65)
    return float(np.clip(angle-wanted,-np.pi/2,np.pi/2))

def allocate(velocity_error_body,attitude_error_body,omega_body,axial_count=9):
    axial=float(np.clip(.35*velocity_error_body[1]/(3.5*axial_count),0,.3))
    side=float(np.clip(-.35*velocity_error_body[2]/6.,0,.5))
    pitch_acceleration=float(np.clip(.4*attitude_error_body[0]-1.2*omega_body[0],-.12,.12))
    correction=pitch_acceleration/(side*(UPPER_PITCH-LOWER_PITCH)) if side>.01 else 0.
    fraction=float(np.clip(NEUTRAL_UPPER+np.clip(correction,-.15,.15),.02,.98))
    upper=side*fraction;lower=side*(1-fraction)
    return dict(axial_power=axial,upper_power=upper,lower_power=lower,
                upper_fraction=fraction,requested_pitch_acceleration=pitch_acceleration,
                predicted_side_pitch_acceleration=UPPER_PITCH*upper+LOWER_PITCH*lower,
                velocity_error_body=np.asarray(velocity_error_body).tolist())
