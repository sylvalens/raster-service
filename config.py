from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    FORMS_T_PATH: str = "/data/FORMS-T"
    GFC_PATH: str = "/data/global-forest-change"
    LIDAR_PATH: str = "/data/lidar-hd"
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    class Config:
        env_file = ".env"

settings = Settings()
