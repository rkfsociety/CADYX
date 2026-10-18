"""Explicit, ordered composition of built-in command extensions.

Composition copies metadata and rejects duplicate names before publishing a
registry. Importing an extension never changes another module or class. The
base command families and extensions share lookup; Core retains the atomic boundary.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from functools import partial
from types import MappingProxyType
from typing import Any, Callable, Mapping

from .command_presentation import BUILTIN_ACTIVITY_ACTIONS, DEFAULT_ACTIVITY_ACTION
from .errors import ValidationError


@dataclass(frozen=True, slots=True)
class CommandExtension:
    name: str
    capabilities: tuple[dict[str, Any], ...] = ()
    handler: Callable[..., dict[str, Any]] | None = None
    middleware: Callable[..., dict[str, Any]] | None = None
    refine: Callable[[tuple[dict[str, Any], ...]], tuple[dict[str, Any], ...]] | None = None
    activity_actions: Mapping[str, str] | None = None


class ExtensionRegistry:
    def __init__(
        self,
        base_capabilities: tuple[dict[str, Any], ...],
        extensions: tuple[CommandExtension, ...],
        *,
        base_handlers: Mapping[str, Callable[..., dict[str, Any]]] | None = None,
    ) -> None:
        contracts = deepcopy(base_capabilities)
        seen: set[str] = set()
        handlers: dict[str, Callable[..., dict[str, Any]]] = {}
        middleware = []
        names: set[str] = set()
        activity_actions: dict[str, str] = {}

        def add_contracts(
            entries: tuple[dict[str, Any], ...],
            actions: Mapping[str, str] | None = None,
        ) -> None:
            entry_names: set[str] = set()
            for entry in entries:
                name = entry["type"]
                if name in seen:
                    raise ValueError(f"Duplicate command registration: {name}.")
                seen.add(name)
                entry_names.add(name)
                action = (
                    actions[name]
                    if actions is not None and name in actions
                    else BUILTIN_ACTIVITY_ACTIONS.get(name, DEFAULT_ACTIVITY_ACTION)
                )
                if not isinstance(action, str) or not action.strip():
                    raise ValueError(f"Command {name} needs a non-empty activity action.")
                activity_actions[name] = action
            if actions is not None:
                unknown_actions = set(actions) - entry_names
                if unknown_actions:
                    names_text = ", ".join(sorted(unknown_actions))
                    raise ValueError(f"Extension activity metadata has no command: {names_text}.")

        add_contracts(contracts)
        if base_handlers is not None:
            if set(base_handlers) != seen:
                raise ValueError("Base command handlers must exactly match base capability names.")
            if any(not callable(handler) for handler in base_handlers.values()):
                raise ValueError("Base command handlers must be callable.")
            handlers.update(base_handlers)
        for extension in extensions:
            if extension.name in names:
                raise ValueError(f"Duplicate extension registration: {extension.name}.")
            names.add(extension.name)
            if extension.capabilities and extension.handler is None:
                raise ValueError(f"Extension {extension.name} declares commands without a handler.")
            entries = deepcopy(extension.capabilities)
            add_contracts(entries, extension.activity_actions)
            if extension.handler is not None:
                for entry in entries:
                    handlers[entry["type"]] = extension.handler
            contracts += entries
            if extension.refine is not None:
                contracts = extension.refine(deepcopy(contracts))
                refined_names = [entry["type"] for entry in contracts]
                if len(refined_names) != len(set(refined_names)) or set(refined_names) != seen:
                    raise ValueError(f"Extension {extension.name} refinement changed registered commands.")
            if extension.middleware is not None:
                middleware.append(extension.middleware)
        self._capabilities = contracts
        self.command_types = frozenset(seen)
        self.handlers = MappingProxyType(handlers)
        self.activity_actions = MappingProxyType(activity_actions)
        self.middleware = tuple(middleware)
        self.extension_names = tuple(extension.name for extension in extensions)

    @property
    def capabilities(self) -> tuple[dict[str, Any], ...]:
        """Return an isolated discovery snapshot, never the registry's metadata."""
        return deepcopy(self._capabilities)

    def activity_action(self, command_name: Any) -> str:
        """Resolve presentation metadata without exposing Core to command-name tables."""
        if not isinstance(command_name, str):
            return DEFAULT_ACTIVITY_ACTION
        return self.activity_actions.get(command_name, DEFAULT_ACTIVITY_ACTION)

    def bind(
        self, system: Any, base_execute: Callable[..., dict[str, Any]] | None = None,
    ) -> Callable[..., dict[str, Any]]:
        def dispatch(document, command, *, progress=None, project_id=None):
            handler = self.handlers.get(command.get("type"))
            if handler is None:
                if base_execute is not None:
                    return base_execute(document, command, progress=progress, project_id=project_id)
                raise ValidationError(f"Unsupported command type: {command.get('type')!r}.")
            return handler(system, document, command, progress=progress, project_id=project_id)

        execute: Callable[..., dict[str, Any]] = dispatch
        # Last middleware is outermost, matching the documented registration order.
        for middleware in self.middleware:
            execute = partial(middleware, execute, system)
        return execute


def builtin_extensions() -> tuple[CommandExtension, ...]:
    # These modules define handlers/metadata only; none imports CommandSystem.
    from . import advanced_features, agent_authoring, curve_editing, manufacturing_features, scan_reverse, stl_reference
    from .capability_refinements import refine_capabilities
    from .semantic_selection import execute_with_selection, refine_capabilities as semantic_capabilities

    return (
        CommandExtension("stl_reference", stl_reference.CAPABILITIES, stl_reference._execute, activity_actions=stl_reference.ACTIVITY_ACTIONS),
        CommandExtension("advanced_features", advanced_features.ADVANCED_CAPABILITIES, advanced_features._execute),
        CommandExtension("agent_authoring", agent_authoring.CAPABILITIES, agent_authoring._execute),
        CommandExtension(
            "curve_editing",
            curve_editing.CAPABILITIES,
            curve_editing._execute,
            activity_actions=curve_editing.ACTIVITY_ACTIONS,
        ),
        CommandExtension("manufacturing_features", manufacturing_features.CAPABILITIES, manufacturing_features._execute),
        CommandExtension("semantic_selection", middleware=execute_with_selection, refine=semantic_capabilities),
        CommandExtension("selector_contracts", refine=refine_capabilities),
        CommandExtension("scan_reverse", scan_reverse.SCAN_REVERSE_CAPABILITIES, scan_reverse._execute,
                         middleware=scan_reverse.execute_with_alignment),
    )
