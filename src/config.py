"""
Configuration management using Pydantic v2 Settings and YAML loader.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, List, Optional, Union
import yaml
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseModel):
    env: str = "production"
    debug: bool = False
    log_level: str = "INFO"
    timezone: str = "Europe/Kyiv"


class TargetEngineConfig(BaseModel):
    code: str
    displacement: float
    fuel: Union[str, List[str]]
    turbo: bool = False


class SearchConfig(BaseModel):
    brand: str = "Audi"
    model: str = "A6"
    generation: str = "C5"
    min_year: int = 1997
    max_year: int = 2005
    target_engines: List[TargetEngineConfig] = Field(default_factory=list)


class DatabaseConfig(BaseModel):
    url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/car_db"
    pool_size: int = 5
    max_overflow: int = 10
    pool_timeout: float = 30.0


class ParserEntryConfig(BaseModel):
    enabled: bool = True
    request_delay: float = 1.5
    timeout: float = 15.0
    max_pages: int = 3


class TelegramParserConfig(ParserEntryConfig):
    api_id: Optional[int] = None
    api_hash: Optional[str] = None
    session_string: Optional[str] = None
    channels: List[str] = Field(default_factory=list)
    max_messages_per_channel: int = 30


class InstagramParserConfig(ParserEntryConfig):
    session_cookie: Optional[str] = None
    accounts: List[str] = Field(default_factory=list)
    max_posts_per_account: int = 12


class ParsersConfig(BaseModel):
    auto_ria: ParserEntryConfig = Field(default_factory=ParserEntryConfig)
    olx: ParserEntryConfig = Field(default_factory=ParserEntryConfig)
    rst: ParserEntryConfig = Field(default_factory=ParserEntryConfig)
    telegram: TelegramParserConfig = Field(default_factory=TelegramParserConfig)
    instagram: InstagramParserConfig = Field(default_factory=InstagramParserConfig)


class TelegramBotConfig(BaseModel):
    bot_token: str = ""
    chat_id: str = ""
    max_photos_per_album: int = 10
    rate_limit_delay: float = 1.2
    send_needs_review: bool = True


class RunnerConfig(BaseModel):
    concurrency_limit: int = 3
    dry_run: bool = False
    deduplication_enabled: bool = True


class Settings(BaseSettings):
    """
    Central application settings combining config/config.yaml and environment variable overrides.
    """
    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        extra="ignore",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # Direct environment variable aliases
    DATABASE_URL: Optional[str] = None
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None
    TELEGRAM_API_ID: Optional[int] = None
    TELEGRAM_API_HASH: Optional[str] = None
    TELEGRAM_SESSION_STRING: Optional[str] = None
    INSTAGRAM_SESSION_COOKIE: Optional[str] = None

    # Sub-sections
    app: AppConfig = Field(default_factory=AppConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    parsers: ParsersConfig = Field(default_factory=ParsersConfig)
    telegram_bot: TelegramBotConfig = Field(default_factory=TelegramBotConfig)
    runner: RunnerConfig = Field(default_factory=RunnerConfig)

    @model_validator(mode="after")
    def sync_env_overrides(self) -> Settings:
        """Propagate explicit root environment variables into respective config models."""
        if self.DATABASE_URL:
            url_str = self.DATABASE_URL
            if url_str.startswith("postgresql://"):
                url_str = "postgresql+asyncpg://" + url_str[len("postgresql://"):]
            if "channel_binding=" in url_str:
                url_str = url_str.replace("channel_binding=require&", "").replace("&channel_binding=require", "")
            if "sslmode=require" in url_str and "ssl=" not in url_str:
                url_str = url_str.replace("sslmode=require", "ssl=require")
            self.database.url = url_str
            self.DATABASE_URL = url_str
        else:
            self.DATABASE_URL = self.database.url

        if self.TELEGRAM_BOT_TOKEN:
            self.telegram_bot.bot_token = self.TELEGRAM_BOT_TOKEN
        if self.TELEGRAM_CHAT_ID:
            self.telegram_bot.chat_id = self.TELEGRAM_CHAT_ID

        if self.TELEGRAM_API_ID is not None:
            self.parsers.telegram.api_id = self.TELEGRAM_API_ID
        if self.TELEGRAM_API_HASH:
            self.parsers.telegram.api_hash = self.TELEGRAM_API_HASH
        if self.TELEGRAM_SESSION_STRING:
            self.parsers.telegram.session_string = self.TELEGRAM_SESSION_STRING

        if self.INSTAGRAM_SESSION_COOKIE:
            self.parsers.instagram.session_cookie = self.INSTAGRAM_SESSION_COOKIE

        return self

    @classmethod
    def load(cls, yaml_path: Optional[Union[str, Path]] = None) -> Settings:
        """
        Loads configuration from YAML file (if provided or default config/config.yaml),
        and applies environment variable overrides.
        """
        config_path = Path(yaml_path) if yaml_path else Path("config/config.yaml")
        yaml_data: dict[str, Any] = {}
        if config_path.is_file():
            with open(config_path, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
                if isinstance(loaded, dict):
                    yaml_data = loaded

        return cls(**yaml_data)


_settings_instance: Optional[Settings] = None


def get_settings(yaml_path: Optional[Union[str, Path]] = None, reload: bool = False) -> Settings:
    """Singleton getter for application settings."""
    global _settings_instance
    if _settings_instance is None or reload:
        _settings_instance = Settings.load(yaml_path)
    return _settings_instance
