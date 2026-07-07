"""Configuration loading: secrets and settings from environment / .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

DialogType = Literal["private", "group", "supergroup", "channel"]

DIALOG_TYPES: tuple[DialogType, ...] = ("private", "group", "supergroup", "channel")


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    session_path: Path
    db_path: Path
    media_dir: Path
    output_dir: Path
    log_level: str = "INFO"
    # Proxy URL for reaching Telegram: socks5://[user:pass@]host:port,
    # socks4://, http://, or mtproxy://SECRET@host:port. None = direct.
    proxy: str | None = None
    # Default dialog selection from TG_INCLUDE_CHATS / TG_EXCLUDE_CHATS /
    # TG_CHAT_TYPES; CLI --include/--exclude/--type flags take precedence.
    chat_filter: ChatFilter | None = None


@dataclass(frozen=True)
class ChatFilter:
    """Dialog selection filter: whitelist / blacklist by ID or exact title, plus type filter.

    Blacklist takes precedence over whitelist. An empty filter matches everything.
    """

    include: tuple[str | int, ...] = field(default=())
    exclude: tuple[str | int, ...] = field(default=())
    types: tuple[DialogType, ...] | None = None

    def matches(self, dialog_id: int, title: str, dialog_type: DialogType) -> bool:
        if self.types is not None and dialog_type not in self.types:
            return False
        if self._in(self.exclude, dialog_id, title):
            return False
        if self.include and not self._in(self.include, dialog_id, title):
            return False
        return True

    @staticmethod
    def _in(items: tuple[str | int, ...], dialog_id: int, title: str) -> bool:
        for item in items:
            if isinstance(item, int) and item == dialog_id:
                return True
            if isinstance(item, str):
                # Numeric strings from CLI args match by ID; anything else by exact title.
                if item.lstrip("-").isdigit() and int(item) == dialog_id:
                    return True
                if item == title:
                    return True
        return False


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _chat_filter_from_env() -> ChatFilter | None:
    include = _split_csv(os.environ.get("TG_INCLUDE_CHATS", ""))
    exclude = _split_csv(os.environ.get("TG_EXCLUDE_CHATS", ""))
    types = _split_csv(os.environ.get("TG_CHAT_TYPES", ""))
    for dialog_type in types:
        if dialog_type not in DIALOG_TYPES:
            raise ConfigError(
                f"TG_CHAT_TYPES contains unknown type {dialog_type!r}; "
                f"valid types: {', '.join(DIALOG_TYPES)}"
            )
    if not include and not exclude and not types:
        return None
    return ChatFilter(
        include=include,
        exclude=exclude,
        types=tuple(types) or None,  # type: ignore[arg-type]
    )


def _desktop_api_credentials() -> tuple[int, str]:
    """Telegram Desktop's official API credentials, taken from opentele.

    Required when the session was imported from a Telegram Desktop tdata folder:
    a session must keep using the credentials it was created with.
    """
    try:
        from opentele.api import API
    except ImportError as exc:
        raise ConfigError(
            "TG_API_CREDENTIALS=desktop requires the 'opentele' package. "
            "Install it with: pip install 'tgexport[tdata]'"
        ) from exc
    api = API.TelegramDesktop
    return int(api.api_id), str(api.api_hash)


def load_config(env_file: Path | None = None) -> Config:
    """Load configuration from the environment, optionally reading a .env file first.

    Raises ConfigError with an actionable message when required variables are absent.
    """
    if env_file is not None:
        load_dotenv(env_file)
    else:
        load_dotenv()

    credentials_mode = os.environ.get("TG_API_CREDENTIALS", "custom").lower()
    if credentials_mode == "desktop":
        api_id, api_hash = _desktop_api_credentials()
    elif credentials_mode != "custom":
        raise ConfigError(
            f"TG_API_CREDENTIALS must be 'custom' or 'desktop', got {credentials_mode!r}"
        )
    else:
        api_id_raw = os.environ.get("TG_API_ID", "")
        api_hash = os.environ.get("TG_API_HASH", "")
        missing = [
            name
            for name, value in (("TG_API_ID", api_id_raw), ("TG_API_HASH", api_hash))
            if not value
        ]
        if missing:
            raise ConfigError(
                f"Missing required environment variable(s): {', '.join(missing)}. "
                "Obtain api_id/api_hash at https://my.telegram.org/apps and put them "
                "in a .env file (see .env.example). Alternatively, import a Telegram "
                "Desktop session (`tgexport login --tdata ...`) and set "
                "TG_API_CREDENTIALS=desktop."
            )
        try:
            api_id = int(api_id_raw)
        except ValueError as exc:
            raise ConfigError(f"TG_API_ID must be an integer, got {api_id_raw!r}") from exc

    return Config(
        api_id=api_id,
        api_hash=api_hash,
        session_path=Path(os.environ.get("TG_SESSION_PATH", "data/session.session")),
        db_path=Path(os.environ.get("TG_DB_PATH", "data/archive.db")),
        media_dir=Path(os.environ.get("TG_MEDIA_DIR", "data/media")),
        output_dir=Path(os.environ.get("TG_OUTPUT_DIR", "output")),
        log_level=os.environ.get("TG_LOG_LEVEL", "INFO"),
        proxy=os.environ.get("TG_PROXY") or None,
        chat_filter=_chat_filter_from_env(),
    )
