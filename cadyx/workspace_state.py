"""Workspace service data lives beside, never inside, the project directory."""

from pathlib import Path

from .errors import ConflictError, ValidationError


def prepare_workspace_state(root: Path) -> Path:
    """Migrate legacy service artifacts while all workspace writers are stopped."""
    # Match ProjectWorkspace's canonical root, including Windows 8.3 aliases.
    root = root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    state = root.parent / f".{root.name}-state"
    if state.is_symlink():
        raise ValidationError("Workspace state directory must not be a symbolic link.")
    state.mkdir(parents=True, exist_ok=True)
    legacy = [entry for entry in root.iterdir()
              if entry.name.startswith((".cadyx-", ".revopoint-"))]
    for entry in legacy:
        if entry.is_symlink():
            raise ValidationError("Legacy workspace state must not be a symbolic link.")
        if (state / entry.name).exists():
            raise ConflictError(f"Workspace state migration destination already exists: {entry.name}")
    for entry in legacy:
        entry.rename(state / entry.name)
    return state
