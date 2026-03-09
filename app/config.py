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
    TEMPLATE_DIR = os.path.join(ROOT_DIR, "app", "templates")

    SITE_NAME = os.getenv("SITE_NAME", "FastAPI Starter")
    BASE_URL = "http://localhost:8000"
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me-in-production-secret-key!")

    # DB
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

    # Auth
    AUTH_URL_PREFIX = "/auth/v1"
    AUTH_REQUIRE_USER_VERIFIED = True
    AUTH_TOKEN_LIFETIME_SECONDS = 7 * 24 * 60 * 60  # 7 days

    # Kafka
    KAFKA_ENABLED: bool = os.getenv("KAFKA_ENABLED", "true").lower() == "true"
    KAFKA_BOOTSTRAP_SERVERS: str = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    KAFKA_CONSUMERS: str = os.getenv(
        "KAFKA_CONSUMERS", "all"
    )  # "all", "none", or comma-separated names

    # Events / RabbitMQ
    EVENT_MODE: str = os.getenv("EVENT_MODE", "all")
    # "all" = run everything in one process (dev)
    # "api" = web server + outbox writes only
    # "relay" = outbox relay only
    # "worker" = event handler worker(s) only
    # "kafka" = Kafka source + outbox writes only
    RABBITMQ_URL: str = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
    EVENT_WORKER_GROUPS: str = os.getenv(
        "EVENT_WORKER_GROUPS", "all"
    )  # "all" or comma-separated group names
    EVENT_WORKER_PREFETCH: int = int(os.getenv("EVENT_WORKER_PREFETCH", "10"))
    EVENT_RELAY_BATCH_SIZE: int = int(os.getenv("EVENT_RELAY_BATCH_SIZE", "100"))
    EVENT_RELAY_POLL_INTERVAL: float = float(
        os.getenv("EVENT_RELAY_POLL_INTERVAL", "5.0")
    )
    EVENT_RELAY_PUBLISH_TIMEOUT: float = float(
        os.getenv("EVENT_RELAY_PUBLISH_TIMEOUT", "30.0")
    )

    # Database pool
    DB_POOL_SIZE: int = int(os.getenv("DB_POOL_SIZE", "5"))
    DB_POOL_MAX_OVERFLOW: int = int(os.getenv("DB_POOL_MAX_OVERFLOW", "10"))

    # WebSocket
    WS_ENABLED: bool = os.getenv("WS_ENABLED", "true").lower() == "true"
    WS_AUTH_METHOD: str = os.getenv("WS_AUTH_METHOD", "none")
    # "none"          = no authentication required
    # "basic"         = HTTP Basic auth during upgrade handshake
    # "token"         = token in query parameter (/ws/{client_id}?token=xxx)
    # "first_message" = client sends {"type": "auth", "token": "xxx"} as first message
    WS_AUTH_TIMEOUT: int = int(os.getenv("WS_AUTH_TIMEOUT", "5"))  # seconds
    WS_HEARTBEAT_INTERVAL: int = int(os.getenv("WS_HEARTBEAT_INTERVAL", "30"))  # seconds
    WS_HEARTBEAT_TIMEOUT: int = int(os.getenv("WS_HEARTBEAT_TIMEOUT", "10"))  # seconds

    # Mail
    RESEND_API_KEY: str = os.getenv("RESEND_API_KEY")
    MAIL_CONFIG = MailConfig(
        # MAIL_SENDER=Resend(api_key=RESEND_API_KEY),
        ADMIN_CONTACT_EMAIL=os.getenv("ADMIN_CONTACT_EMAIL"),
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
