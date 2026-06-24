from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "mysql+aiomysql://root:mysql_ktXzzs@127.0.0.1:3306/idea_king"
    secret_key: str = "timeline-jwt-secret-change-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440  # 24 hours
    admin_session_secret: str = "admin-session-secret-change-in-production"

    # S3 / Garagen
    s3_endpoint: str = "127.0.0.1:3900"
    s3_access_key: str = "GKcda0ccd3a856a1c1e1bd46b7"
    s3_secret_key: str = "61a143bedcaa3379ced011172aae03ce1048e2b4ed44c8394a418f03af4db00a"
    s3_bucket: str = "idea-king"
    s3_region: str = "garage"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
