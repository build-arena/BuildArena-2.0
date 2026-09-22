"""User-facing control meanings for fuel connectors."""

CONTROL_SEMANTICS = {
    96: {"descriptions": {
        "LinkKey": "Toggles the fuel line between linked (open) and unlinked (closed)."
    }},
    98: {"descriptions": {
        "DetachKey": "Releases the module held by the fuel coupler."
    }},
    103: {
        "descriptions": {
            "PumpKey": "Runs or stops the pump, transferring fuel at the configured flow speed."
        },
        "aliases": {"pump": "PumpKey"},
        "sliders": {
            "flow-speed": "Fuel transfer speed. Besiege 1.91 accepts 0.1 to 25 and defaults to 20."
        },
    },
}
