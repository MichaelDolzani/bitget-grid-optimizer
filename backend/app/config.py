from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Bitget (used only for single-bot PoC mode)
    bitget_api_key: str = ""
    bitget_api_secret: str = ""
    bitget_passphrase: str = ""
    bot_id: str = ""
    symbol: str = "BTCUSDT"
    bot_lower_price: float = 0.0
    bot_upper_price: float = 0.0
    bot_grid_num: int = 0
    bot_invest_amount: float = 0.0

    # Optimizer defaults
    check_interval_minutes: int = 30
    shift_threshold_pct: float = 5.0
    atr_multiplier: float = 2.5
    sigma_multiplier: float = 1.5
    step_target_pct: float = 0.8
    max_grid_count: int = 150
    cooldown_minutes: int = 60
    ttm_squeeze_enabled: bool = True
    volatility_spike_multiplier: float = 1.5
    volatility_spike_range_expand: float = 1.20
    grid_type: str = "geometric"

    # Fund manager
    min_add_funds_usdt: float = 10.0
    fund_check_interval_hours: int = 6
    reserve_pct: float = 2.0

    # Web
    secret_key: str = "changeme"
    admin_email: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost/auth/callback"
    fernet_key: str = ""

    # DB
    database_url: str = "sqlite:///./bitget.db"

    # Notifications
    gchat_webhook_url: str = ""


settings = Settings()
