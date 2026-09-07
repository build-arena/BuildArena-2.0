"""Explicit control addresses verified against the ToolKit 2.0.9 Inspector.

Only observed KeyList slots are named here. A description alone does not
establish an address (for example Spring.UnwindKey and Engine.OffKey).
Empty mappings mean the Inspector has not exposed a controllable KeyList.
Changing description order must never change an actuator address.
"""

KEYLIST_BINDING_VERSION = 1

KEYLIST_BINDINGS = {
    0: {},  # KeyList[0] is explicitly ignored by its descriptor.
    2: {"ForwardKey": 0, "BackwardKey": 1},
    4: {"ExplodeKey": 0},
    9: {"ContractKey": 0},
    11: {"ShootKey": 0},
    13: {"leftKey": 0, "rightKey": 1},
    14: {"FlyKey": 0},
    16: {},
    17: {},
    18: {"ExtendKey": 0},
    21: {"IgniteKey": 0},
    22: {},
    27: {"detachKey": 0, "attachKey": 1},
    28: {"leftKey": 0, "rightKey": 1},
    35: {},
    39: {"ForwardKey": 0, "BackwardKey": 1},
    45: {"ContractKey": 0, "UnwindKey": 1},
    46: {"ForwardKey": 0, "BackwardKey": 1},
    48: {"ForwardKey": 0, "BackwardKey": 1},
    53: {"ShootKey": 0},
    54: {"DetonateKey": 0},
    56: {"ShootKey": 0},
    57: {},
    58: {},
    59: {"LaunchKey": 0},
    61: {"FireKey": 0},
    62: {"VacuumKey": 0},
    65: {},
    66: {},
    67: {},
    68: {},
    69: {},
    70: {},
    74: {"MainKey": 0, "SecondaryKey": 1},
    75: {},
    77: {"ActivateKey": 0},
    78: {"RaiseSail": 0, "LowerSail": 1},
    79: {"leftKey": 0, "rightKey": 1},
    80: {"ForwardKey": 0, "BackwardKey": 1},
    82: {"increaseBuoyancy": 0, "decreaseBuoyancy": 1},
    83: {"increaseBuoyancy": 0, "decreaseBuoyancy": 1},
    84: {"ShootKey": 0, "RetractKey": 1},
    88: {"ForwardKey": 0},
    90: {"ThrustKey": 0},
    91: {"LeftKey": 0, "RightKey": 1},
    93: {},
    94: {"leftKey": 0, "rightKey": 1},
    95: {"leftKey": 0, "rightKey": 1},
    96: {"LinkKey": 0},
    97: {"deployKey": 0},
    98: {"DetachKey": 0},
    100: {"ForwardKey": 0, "BackwardKey": 1},
    101: {"LeftKey": 0, "RightKey": 1},
    102: {"FireKey": 0},
}
