"""Settings for db_maintenance: independent of the other two services."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class DBMaintenanceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    log_dir: Path = Path("logs")
    backup_dir: Path = Path("backups")
    metrics_file: Path = Path("metrics/db_maintenance.jsonl")


settings = DBMaintenanceSettings()
