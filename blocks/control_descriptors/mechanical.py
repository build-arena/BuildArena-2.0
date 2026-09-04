"""User-facing control meanings for mechanical and motion blocks."""

CONTROL_SEMANTICS = {
    0: {
        "descriptions": {},
        "ignored": {
            "channel_0": "Starting Block has no user-controllable property."
        },
    },
    2: {
        "descriptions": {
            "ForwardKey": "Drives the powered wheel forward.",
            "BackwardKey": "Drives the powered wheel backward.",
        },
        "sliders": {
            "speed": "Maximum powered-wheel speed.",
            "damper": "Wheel damping.",
        },
    },
    16: {
        "descriptions": {},
        "sliders": {
            "spring": "Suspension spring stiffness.",
            "damper": "Suspension damping.",
        },
    },
    35: {
        "descriptions": {},
        "sliders": {
            "mass": "Ballast mass.",
        },
    },
    4: {"descriptions": {
        "ExplodeKey": "Separates the decoupler from its mounted connection and ejects it."
    }},
    9: {"descriptions": {
        "ContractKey": "Contracts the spring to pull its endpoints together.",
        "UnwindKey": "Unwinds the spring in the opposite direction.",
    }},
    13: {"descriptions": {
        "leftKey": "Rotates the powered steering block in its left control direction.",
        "rightKey": "Rotates the powered steering block in its right control direction.",
    }},
    14: {"descriptions": {
        "FlyKey": "Activates the fan's directed lift."
    }},
    17: {"descriptions": {
        "ForwardKey": "Spins the circular saw in its forward direction.",
        "BackwardKey": "Spins the circular saw in its reverse direction.",
    }},
    18: {"descriptions": {
        "ExtendKey": "Extends the piston while held; releasing it lets the piston retract."
    }},
    22: {"descriptions": {
        "ForwardKey": "Drives powered rotation in the forward direction.",
        "BackwardKey": "Drives powered rotation in the reverse direction.",
    }},
    27: {"descriptions": {
        "detachKey": "Releases the object currently held by the grabber.",
        "attachKey": "Commands the grabber to attach to a reachable object.",
    }},
    28: {"descriptions": {
        "leftKey": "Rotates the steering hinge in its left control direction.",
        "rightKey": "Rotates the steering hinge in its right control direction.",
    }},
    39: {"descriptions": {
        "ForwardKey": "Drives the powered cog in its forward rotation direction.",
        "BackwardKey": "Drives the powered cog in its reverse rotation direction.",
    }},
    45: {"descriptions": {
        "ContractKey": "Reels in the rope winch.",
        "UnwindKey": "Pays out the rope winch.",
    }},
    46: {"descriptions": {
        "ForwardKey": "Drives the large powered wheel forward.",
        "BackwardKey": "Drives the large powered wheel backward.",
    }},
    48: {"descriptions": {
        "ForwardKey": "Spins the drill in its forward direction.",
        "BackwardKey": "Spins the drill in its reverse direction.",
    }},
    57: {"descriptions": {
        "UnpinKey": "Releases the pin connection."
    }},
    74: {
        "descriptions": {
            "MainKey": "Raises the square balloon (default key U).",
            "SecondaryKey": "Lowers the square balloon (default key J).",
        },
        "aliases": {"primaryKey": "MainKey"},
    },
    77: {"descriptions": {
        "ActivateKey": "Triggers the armed metal jaw to snap shut."
    }},
    80: {"descriptions": {
        "ForwardKey": "Drives the nautical screw for forward propulsion.",
        "BackwardKey": "Drives the nautical screw for reverse propulsion.",
    }},
    82: {"descriptions": {
        "increaseBuoyancy": "Increases the buoyancy setting.",
        "decreaseBuoyancy": "Decreases the buoyancy setting.",
    }},
    83: {"descriptions": {
        "increaseBuoyancy": "Increases the barrel's buoyancy setting.",
        "decreaseBuoyancy": "Decreases the barrel's buoyancy setting.",
    }},
    88: {"descriptions": {
        "ForwardKey": "Spins the flywheel in its forward direction.",
        "BackwardKey": "Spins the flywheel in its reverse direction.",
    }},
    94: {"descriptions": {
        "leftKey": "Folds the grid fin closed.",
        "rightKey": "Deploys the grid fin open.",
    }},
    95: {"descriptions": {
        "leftKey": "Deflects the steering fin toward its left control limit.",
        "rightKey": "Deflects the steering fin toward its right control limit.",
    }},
    100: {"descriptions": {
        "ForwardKey": "Drives the space wheel forward.",
        "BackwardKey": "Drives the space wheel backward.",
    }},
    101: {
        "descriptions": {
            "LeftKey": "Spins the reaction wheel in its positive direction.",
            "RightKey": "Spins the reaction wheel in its negative direction.",
        },
        "sliders": {
            "speed": "Reaction-wheel spin rate / torque scale.",
            "damper": "Reaction-wheel damping.",
        },
    },
}
