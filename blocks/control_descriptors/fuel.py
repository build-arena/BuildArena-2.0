"""User-facing control meanings for fuel connectors and the fuel pump."""

CONTROL_SEMANTICS = {
    96: {"descriptions": {
        "LinkKey": "Toggles the fuel line between linked (open) and unlinked (closed)."
    }},
    98: {"descriptions": {
        "DetachKey": "Releases the module held by the fuel coupler."
    }},
    103: {
        "descriptions": {
            "PumpKey": "While held, pumps fuel from the mounted fuel system and the two side faces into the fuel system on the free end face. With Toggle Mode on, the key latches instead.",
        },
        "sliders": {
            "flow-speed": "Fuel pump speed. Logarithmic slider from 0.1 to 25; default is 20.",
        },
    },
}
