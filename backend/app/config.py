"""Runtime configuration, read from environment variables."""
from __future__ import annotations

import os
import secrets
from pathlib import Path


class Settings:
    def __init__(self) -> None:
        self.data_dir = Path(os.environ.get("BENCHTOP_DATA_DIR", "./data")).resolve()
        self.database_url = os.environ.get(
            "BENCHTOP_DATABASE_URL", f"sqlite:///{self.data_dir / 'benchtop.db'}"
        )
        self.attachments_dir = self.data_dir / "attachments"
        secret_file = self.data_dir / ".secret_key"
        env_secret = os.environ.get("BENCHTOP_SECRET_KEY")
        if env_secret:
            self.secret_key = env_secret
        else:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            if not secret_file.exists():
                secret_file.write_text(secrets.token_hex(32))
            self.secret_key = secret_file.read_text().strip()
        self.token_ttl_hours = int(os.environ.get("BENCHTOP_TOKEN_TTL_HOURS", "12"))
        self.admin_username = os.environ.get("BENCHTOP_ADMIN_USER", "admin")
        self.admin_password = os.environ.get("BENCHTOP_ADMIN_PASSWORD", "admin")
        self.static_dir = os.environ.get("BENCHTOP_STATIC_DIR")  # built frontend
        self.max_upload_mb = int(os.environ.get("BENCHTOP_MAX_UPLOAD_MB", "50"))
        # Backups: point BENCHTOP_BACKUP_DIR at an iCloud/Dropbox folder to keep copies off-machine
        self.backup_dir = Path(os.environ.get("BENCHTOP_BACKUP_DIR", self.data_dir / "backups")).expanduser().resolve()
        self.backup_keep = int(os.environ.get("BENCHTOP_BACKUP_KEEP", "14"))
        self.backup_interval_hours = float(os.environ.get("BENCHTOP_BACKUP_INTERVAL_HOURS", "24"))
        obs = os.environ.get("BENCHTOP_OBSIDIAN_DIR")
        self.obsidian_dir = Path(obs).expanduser().resolve() if obs else None  # hourly Markdown sync target
        self.port = int(os.environ.get("BENCHTOP_PORT", "8000"))
        # Safety data from PubChem (public GHS classifications); set BENCHTOP_PUBCHEM=off to work fully offline
        self.pubchem_enabled = os.environ.get("BENCHTOP_PUBCHEM", "on").lower() not in ("0", "off", "false", "no")
        self.safety_refresh_days = float(os.environ.get("BENCHTOP_SAFETY_REFRESH_DAYS", "30"))
        # NMR shift table: re-check NMRShiftDB2 monthly and rebuild when it publishes a newer export
        self.nmr_auto_update = os.environ.get("BENCHTOP_NMR_AUTO_UPDATE", "on").lower() not in ("0", "off", "false", "no")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.attachments_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
