"""Bounded in-process cache policy shared by CADYX runtime caches."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from typing import TypeVar


KeyT = TypeVar("KeyT", bound=Hashable)
ValueT = TypeVar("ValueT")


@dataclass(frozen=True)
class RuntimeCachePolicy:
    """Central entry and byte budgets for revision-bound runtime caches."""

    validated_document_entries: int = 32
    scene_entries: int = 8
    png_entries: int = 8
    png_bytes: int = 32 * 1024 * 1024
    viewport_snapshot_entries: int = 8
    viewport_snapshot_bytes: int = 32 * 1024 * 1024

    def __post_init__(self) -> None:
        for name, value in (
            ("validated_document_entries", self.validated_document_entries),
            ("scene_entries", self.scene_entries),
            ("png_entries", self.png_entries),
            ("png_bytes", self.png_bytes),
            ("viewport_snapshot_entries", self.viewport_snapshot_entries),
            ("viewport_snapshot_bytes", self.viewport_snapshot_bytes),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer.")


def trim_lru_entries(cache: OrderedDict[KeyT, ValueT], max_entries: int) -> None:
    """Evict oldest entries until an LRU cache satisfies its entry budget."""

    while len(cache) > max_entries:
        cache.popitem(last=False)


def remember_bounded_bytes(
    cache: OrderedDict[KeyT, ValueT],
    key: KeyT,
    value: ValueT,
    *,
    current_bytes: int,
    max_entries: int,
    max_bytes: int,
    size_of: Callable[[ValueT], int],
) -> int:
    """Store one LRU value and return exact retained-byte accounting."""

    value_bytes = size_of(value)
    had_previous = key in cache
    previous_bytes = size_of(cache[key]) if had_previous else 0
    if had_previous:
        del cache[key]
        current_bytes -= previous_bytes
    if max_entries == 0:
        cache.clear()
        return 0
    # Do not evict reusable entries to make room for a value that cannot fit.
    if value_bytes > max_bytes:
        return current_bytes
    cache[key] = value
    current_bytes += value_bytes
    while cache and (len(cache) > max_entries or current_bytes > max_bytes):
        _, evicted = cache.popitem(last=False)
        current_bytes -= size_of(evicted)
    return current_bytes
