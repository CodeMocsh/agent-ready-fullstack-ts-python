"""What each module under `app/models/` may import, and no others.

The project owns this module: the template writes it once and an update never touches it, so add
a line here with each new module. `tests/models/test_layering.py` holds `app/models/` to it.
Each entry lists only the imports that module makes today. `docs/adr/template/0007`.
"""

LAYERS: dict[str, frozenset[str]] = {
    "shared": frozenset(),
    "tasks": frozenset(),
    "client_events": frozenset(),
}
