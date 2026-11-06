"""What this process admits for one actor. The project owns this module: the template writes it
once and an update never touches it, so set your own numbers here.

Each limit is per process. Under the identity stub every request is one actor, so these limits
hold for everybody at once, in each process. `app/admission.py` is the mechanism.
`docs/adr/template/0023`.
"""

from typing import Final

from app.admission import Limits as Limits

LIMITS: Final = Limits(in_flight=8, per_second=5.0, burst=40, streams=4)
