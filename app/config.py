import os

from app.mail import MailConfig, Resend


try:
    from dotenv import load_dotenv
except (ImportError, ModuleNotFoundError):
    pass
else:
    load_dotenv()


class Config:
    ROOT_DIR = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))

    SITE_NAME = os.getenv("SITE_NAME", "FastAPI Starter")
    BASE_URL = "http://localhost:8000"

    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me-secret!")

    SQL_DB_URL: str = os.getenv(
        "SQL_DB_URL",
        default="{engine}://{user}:{pw}@{host}:{port}/{db}".format(
            engine=os.getenv("SQL_DB_ENGINE", "postgresql+asyncpg"),
            user=os.getenv("SQL_DB_USER", "fastapi"),
            pw=os.getenv("SQL_DB_PASSWORD", "fastapi"),
            host=os.getenv("SQL_DB_HOST", "127.0.0.1"),
            port=os.getenv("SQL_DB_PORT", 5432),
            db=os.getenv("SQL_DB_NAME", "fastapi"),
        ),
    )

    AUTH_URL_PREFIX = "/auth/v1"
    AUTH_REQUIRE_USER_VERIFIED = True
    AUTH_TOKEN_LIFETIME_SECONDS = 7 * 24 * 60 * 60  # 7 days

    RESEND_API_KEY: str = os.getenv("RESEND_API_KEY")
    TEMPLATE_DIR = os.path.join(ROOT_DIR, "app", "templates")
    MAIL_CONFIG = MailConfig(
        # MAIL_SENDER=Resend(api_key=RESEND_API_KEY),
        MAIL_SERVER="localhost",
        MAIL_PORT=1025,
        MAIL_USERNAME="",
        MAIL_PASSWORD="",
        USE_CREDENTIALS=False,
        MAIL_FROM="delivered@resend.dev",
        MAIL_FROM_NAME=SITE_NAME,
        MAIL_STARTTLS=False,
        MAIL_SSL_TLS=False,
        VALIDATE_CERTS=False,
        TEMPLATE_FOLDER=TEMPLATE_DIR,
    )
