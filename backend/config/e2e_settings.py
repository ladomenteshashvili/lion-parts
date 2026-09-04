import os

os.environ.setdefault("SECRET_KEY", "e2e-secret-key")
os.environ.setdefault("DEBUG", "True")
os.environ.setdefault("ALLOWED_HOSTS", "localhost,127.0.0.1")

# These are only needed so base settings.py can import safely.
# After import, DATABASES is overridden to sqlite below.
os.environ.setdefault("DB_NAME", "e2e")
os.environ.setdefault("DB_USER", "e2e")
os.environ.setdefault("DB_PASSWORD", "e2e")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "5432")

os.environ.setdefault("PARTS_PROVIDER", "demo")
os.environ.setdefault("SENDER_GE_ENABLED", "False")

from .settings import *  # noqa: F401,F403,E402

DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "e2e.sqlite3",
    }
}

CORS_ALLOWED_ORIGINS = [
    "http://localhost:4173",
    "http://127.0.0.1:4173",
]

PARTS_PROVIDER = "demo"
SENDER_GE_ENABLED = False

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]
