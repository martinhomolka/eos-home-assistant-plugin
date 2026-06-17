"""EOS program slot labels (climate preset modes)."""

from __future__ import annotations

from typing import Any

from .coordinator import EosDataUpdateCoordinator


def profile_slot_label_pairs(coordinator: EosDataUpdateCoordinator, device_id: str) -> list[tuple[int, str]]:
    """Return ordered (slot index, UI label) for each program slot."""
    state = coordinator.get_device_shadow_state(device_id)
    raw_profiles = state.get("profiles")
    profiles: dict[str, Any] = raw_profiles if isinstance(raw_profiles, dict) else {}

    raw_labels: list[tuple[int, str]] = []
    # Range 4, because there are 4 profiles in total
    for i in range(4):
        base = f"Profile {i}"
        prof = profiles.get(str(i))
        name = prof.get("name") if isinstance(prof, dict) else None
        base = name.strip() if name is not None and isinstance(name, str) and name.strip() else base
        raw_labels.append((i, base))
    return raw_labels
