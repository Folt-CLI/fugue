"""Settings of the work flow."""

from pydantic_settings import SettingsConfigDict

from fugue.config import Config


class WorkConfig(Config):
    """FUGUE_WORK_STARTED_STATUS: the project status a ticket moves to when work starts.
    FUGUE_WORK_STATUS_FIELD: the name of the project field that holds the status."""

    model_config = SettingsConfigDict(env_prefix="FUGUE_WORK_")

    started_status: str = "S4: In Implementation"
    status_field: str = "Status"
