"""Base command family registry, composed before extension contracts are published."""
from __future__ import annotations

from typing import Any, Callable
from .command_sketches import COMMAND_HANDLERS as SKETCHES_HANDLERS
from .command_sketch_profiles import COMMAND_HANDLERS as SKETCH_PROFILES_HANDLERS
from .command_sketch_curves import COMMAND_HANDLERS as SKETCH_CURVES_HANDLERS
from .command_history import COMMAND_HANDLERS as HISTORY_HANDLERS
from .command_bodies import COMMAND_HANDLERS as BODIES_HANDLERS
from .command_parameters import COMMAND_HANDLERS as PARAMETERS_HANDLERS
from .command_scan import COMMAND_HANDLERS as SCAN_HANDLERS
from .command_delivery import COMMAND_HANDLERS as DELIVERY_HANDLERS
from .command_assembly import COMMAND_HANDLERS as ASSEMBLY_HANDLERS


COMMAND_FAMILIES = (
    ("sketches", SKETCHES_HANDLERS),
    ("sketch_profiles", SKETCH_PROFILES_HANDLERS),
    ("sketch_curves", SKETCH_CURVES_HANDLERS),
    ("history", HISTORY_HANDLERS),
    ("bodies", BODIES_HANDLERS),
    ("parameters", PARAMETERS_HANDLERS),
    ("scan", SCAN_HANDLERS),
    ("delivery", DELIVERY_HANDLERS),
    ("assembly", ASSEMBLY_HANDLERS),
)


def base_command_handlers() -> dict[str, Callable[..., dict[str, Any]]]:
    handlers = {}
    for family, entries in COMMAND_FAMILIES:
        for name, handler in entries.items():
            if name in handlers:
                raise ValueError(f"Duplicate base command registration: {name} ({family}).")
            handlers[name] = handler
    return handlers
