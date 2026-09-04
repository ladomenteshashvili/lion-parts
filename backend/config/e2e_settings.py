from .settings import *  # noqa: F401,F403

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
