"""Export the CLI's validated semantic mapping for one live SDK run."""

from buildarena.control_descriptor_loader import load_control_semantics
from blocks.control_descriptors.keylist_bindings import KEYLIST_BINDING_VERSION
from controller_sdk.channel_bindings import BINDINGS_SCHEMA


def build_control_bindings(blocks, channels, *, run_id: str) -> dict:
    grouped = {}
    for channel in channels:
        grouped.setdefault(channel.block_guid, []).append(channel)
    rows = []
    for block in blocks:
        semantics = load_control_semantics(block_id=int(block.block_id))
        ignored = [] if semantics is None else [
            int(name[8:]) for name in semantics.ignored if name.startswith("channel_")
        ]
        rows.append(dict(
            guid=block.guid, block_id=int(block.block_id), local_index=block.local_index,
            ignored_keylist_indices=ignored,
            channels=[dict(keylist_index=c.keylist_index, name=c.channel, aliases=list(c.aliases))
                      for c in grouped.get(block.guid, [])],
        ))
    return dict(schema=BINDINGS_SCHEMA, keylist_binding_version=KEYLIST_BINDING_VERSION,
                run_id=run_id, blocks=rows)
