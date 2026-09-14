from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="UGA_BUS_", extra="ignore")

    app_name: str = "UGA Bus"
    app_version: str = "0.1.0"
    database_url: str = "sqlite:///./data/uga_bus.sqlite3"
    data_dir: Path = Path("./data")
    static_gtfs_url: str = "https://passio3.com/uga/passioTransit/gtfs/google_transit.zip"
    vehicle_positions_url: str = "https://passio3.com/uga/passioTransit/gtfs/realtime/vehiclePositions"
    trip_updates_url: str = "https://passio3.com/uga/passioTransit/gtfs/realtime/tripUpdates"
    service_alerts_url: str = "https://passio3.com/uga/passioTransit/gtfs/realtime/serviceAlerts"
    vehicle_poll_seconds: int = 8
    trip_update_poll_seconds: int = 10
    alert_poll_seconds: int = 45
    static_poll_seconds: int = 3600
    request_timeout_seconds: float = 12.0
    stale_after_seconds: int = 45
    detailed_observation_retention_days: int = 180
    raw_snapshot_retention_days: int = 7
    max_arrivals_per_stop: int = 30
    cors_origins: str = ""
    diagnostics_token: str | None = None
    user_agent: str = "UGABus/0.1 (+https://bus.loganpeterson.org; transit-data collector)"

    @property
    def sqlite_path(self) -> Path | None:
        prefix = "sqlite:///"
        return Path(self.database_url.removeprefix(prefix)) if self.database_url.startswith(prefix) else None


settings = Settings()
