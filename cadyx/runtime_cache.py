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

    if type(max_entries) is not int or max_entries < 0:
        raise ValueError("Cache entry budget must be a non-negative integer.")
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

    if any(type(amount) is not int or amount < 0
           for amount in (current_bytes, max_entries, max_bytes)):
        raise ValueError("Cache budgets and byte accounting must be non-negative integers.")
    # Disabled caches must not evaluate an expensive or failing size callback.
    # Discard any previously retained entries when the policy is set to zero.
    if max_entries == 0:
        cache.clear()
        return 0
    value_bytes = size_of(value)
    if type(value_bytes) is not int or value_bytes < 0:
        raise ValueError("Cache item size must be a non-negative integer.")
    had_previous = key in cache
    previous_bytes = size_of(cache[key]) if had_previous else 0
    if type(previous_bytes) is not int or previous_bytes < 0:
        raise ValueError("Cache item size must be a non-negative integer.")
    if previous_bytes > current_bytes:
        raise ValueError("Cache byte accounting is smaller than the replaced item.")
    retained_bytes = current_bytes - previous_bytes
    # An oversized replacement removes only its old entry; other entries remain.
    if value_bytes > max_bytes:
        if had_previous:
            del cache[key]
        return retained_bytes

    # Measure every required eviction before mutating the cache. A failed or
    # invalid size callback must not leave a half-evicted LRU or stale accounting.
    retained_bytes += value_bytes
    retained_entries = len(cache) + (0 if had_previous else 1)
    evictions: list[KeyT] = []
    for old_key, old_value in cache.items():
        if retained_entries <= max_entries and retained_bytes <= max_bytes:
            break
        if old_key == key:
            continue  # The replacement moves to the newest LRU position.
        old_bytes = size_of(old_value)
        if type(old_bytes) is not int or old_bytes < 0:
            raise ValueError("Cache item size must be a non-negative integer.")
        if old_bytes > retained_bytes - value_bytes:
            raise ValueError("Cache byte accounting is smaller than the evicted items.")
        evictions.append(old_key)
        retained_bytes -= old_bytes
        retained_entries -= 1

    if had_previous:
        del cache[key]
    cache[key] = value
    for old_key in evictions:
        del cache[old_key]
    return retained_bytes
