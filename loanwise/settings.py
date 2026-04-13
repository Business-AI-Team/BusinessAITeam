"""
Django settings for LoanWise — API-first, i18n, LLM hooks, Swagger/Redoc.
"""

from pathlib import Path
import os

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-insecure-change-me-in-production")

DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() in ("1", "true", "yes")

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get(
        "DJANGO_ALLOWED_HOSTS",
        "192.168.1.218,127.0.0.1,localhost",
    ).split(",")
    if h.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "drf_spectacular",
    "corsheaders",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "loanwise.middleware.SyncPreferredLanguageMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "loanwise.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
                "loanwise.context_processors.loanwise_globals",
            ],
        },
    },
]

WSGI_APPLICATION = "loanwise.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": os.environ.get("DJANGO_DB_ENGINE", "django.db.backends.sqlite3"),
        "NAME": os.environ.get("DJANGO_DB_NAME", str(BASE_DIR / "db.sqlite3")),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "fr"
TIME_ZONE = "Europe/Paris"
USE_I18N = True
USE_TZ = True

LANGUAGES = [
    ("fr", "Français"),
    ("en", "English"),
]

LOCALE_PATHS = [BASE_DIR / "locale"]

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Liveness video uploads (webm) — override via env if needed
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.environ.get("DATA_UPLOAD_MAX_MEMORY_SIZE", str(15 * 1024 * 1024)))
FILE_UPLOAD_MAX_MEMORY_SIZE = int(os.environ.get("FILE_UPLOAD_MAX_MEMORY_SIZE", str(15 * 1024 * 1024)))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "core.Account"

EMAIL_BACKEND = os.environ.get(
    "DJANGO_EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend",
)
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "LoanWise <noreply@loanwise.local>")
FRONTEND_BASE_URL = os.environ.get("FRONTEND_BASE_URL", "http://127.0.0.1:8000")


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name, "")
    if v.lower() in ("1", "true", "yes", "on"):
        return True
    if v.lower() in ("0", "false", "no", "off"):
        return False
    return default


# Production: exiger une e-mail vérifiée pour lancer le pipeline. En DEBUG, False par défaut (hackathon / local).
LOANWISE_REQUIRE_EMAIL_VERIFICATION = _env_bool(
    "LOANWISE_REQUIRE_EMAIL_VERIFICATION",
    default=not DEBUG,
)
# En DEBUG, marquer l’e-mail comme vérifié à l’inscription (aucun SMTP requis pour tester le flux).
LOANWISE_AUTO_VERIFY_EMAIL_IN_DEBUG = _env_bool(
    "LOANWISE_AUTO_VERIFY_EMAIL_IN_DEBUG",
    default=DEBUG,
)

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.TokenAuthentication",
    ],
    # Views that require auth set `permission_classes = [IsAuthenticated]` explicitly (API-first, open docs).
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "core.exception_handler.loanwise_exception_handler",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "LoanWise API",
    "DESCRIPTION": "Smart Loan Eligibility Checker — REST API (API-first).",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

CORS_ALLOW_ALL_ORIGINS = DEBUG
CORS_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get("CORS_ALLOWED_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000").split(",")
    if o.strip()
]

# --- LoanWise business / AI ---
LOANWISE_CURRENCY = os.environ.get("LOANWISE_CURRENCY", "EUR")
LOANWISE_INTEREST_RATE_ANNUAL = float(os.environ.get("LOANWISE_INTEREST_RATE_ANNUAL", "0.05"))
LOANWISE_APPROVAL_THRESHOLD = float(os.environ.get("LOANWISE_APPROVAL_THRESHOLD", "55"))
LOANWISE_DELETE_FILES_AFTER_ANALYSIS = os.environ.get("LOANWISE_DELETE_FILES_AFTER_ANALYSIS", "true").lower() in (
    "1",
    "true",
    "yes",
)
# Florence-2 (heavy GPU/CPU load); keep off unless explicitly enabled (see requirements-ai.txt).
LOANWISE_ENABLE_FLORENCE = _env_bool("LOANWISE_ENABLE_FLORENCE", default=False)

# LLM: openai | ollama | none
LOANWISE_LLM_PROVIDER = os.environ.get("LOANWISE_LLM_PROVIDER", "none")
LOANWISE_OPENAI_MODEL = os.environ.get("LOANWISE_OPENAI_MODEL", "gpt-4o-mini")
LOANWISE_OLLAMA_BASE_URL = os.environ.get("LOANWISE_OLLAMA_BASE_URL", "http://127.0.0.1:11434")
LOANWISE_OLLAMA_MODEL = os.environ.get("LOANWISE_OLLAMA_MODEL", "llama3")
LOANWISE_USE_LANGGRAPH = os.environ.get("LOANWISE_USE_LANGGRAPH", "false").lower() in ("1", "true", "yes")

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/dashboard/"
LOGOUT_REDIRECT_URL = "/"
