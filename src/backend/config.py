from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    """
    Every environment-dependent value lives here — never as a magic
    literal scattered through business or infra code.
    """
    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_")

    model_name: str = "hustvl/yolos-tiny"
    default_threshold: float = 0.5
    device_preference: str = "auto"   # auto | cpu | cuda | mps
    frame_queue_maxsize: int = 1

@lru_cache
def get_settings() -> Settings:
    # lru_cache turns this into a cheap singleton: env vars are parsed
    # and validated exactly once, then reused — and it's still trivially
    # override-able in tests via dependency_overrides.
    print(Settings())
    return Settings()