"""What production may not carry. The project owns this module: the template writes it once
and an update never touches it, so add your own findings and variables here.

`refuse_development_settings` is what `app/lifespan.py` calls before anything is built. The
mechanism is re-exported, so the project's own code imports all of it from here.
`docs/adr/template/0017`.
"""

from app.deployment import (
    DATABASE_URL_ENV as DATABASE_URL_ENV,
    DEVELOPMENT as DEVELOPMENT,
    ENVIRONMENT_ENV as ENVIRONMENT_ENV,
    PRODUCTION as PRODUCTION,
    DevelopmentSettingInProduction as DevelopmentSettingInProduction,
    UnknownEnvironment as UnknownEnvironment,
    in_development as in_development,
    refuse_in_production,
    stated as stated,
)


def refuse_development_settings() -> None:
    """Refuse to serve production carrying anything only the development loop should."""
    refuse_in_production(_ephemeral_substrate())


def _ephemeral_substrate() -> list[str]:
    if stated(DATABASE_URL_ENV) != "":
        return []
    return [
        f"{DATABASE_URL_ENV} is unset, so every row lives in this process's memory and is gone "
        f"when it exits. Set it, or set {ENVIRONMENT_ENV}={DEVELOPMENT} if this is not a "
        f"deployment."
    ]
