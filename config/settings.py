import os
from ipaddress import ip_network
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured


BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.getenv("DJANGO_DEBUG", "0") == "1"
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "development-only-change-before-deployment"
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY is required when DJANGO_DEBUG is off")

if not DEBUG and not os.getenv("DJANGO_ALLOWED_HOSTS"):
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS is required when DJANGO_DEBUG is off")
ALLOWED_HOSTS = [host.strip() for host in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if host.strip()]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "training",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": [
            "django.template.context_processors.request",
            "django.template.context_processors.csrf",
            "training.context_processors.account_navigation",
        ]},
    }
]
WSGI_APPLICATION = "config.wsgi.application"

if not os.getenv("DATABASE_URL"):
    raise ImproperlyConfigured("DATABASE_URL is required")
DATABASES = {"default": dj_database_url.parse(os.environ["DATABASE_URL"], conn_max_age=60)}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_TZ = True
ORG_TIME_ZONE = os.getenv("ORG_TIME_ZONE", "America/New_York")
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {"staticfiles": {"BACKEND": "config.storage.VersionedStaticFilesStorage"}}
# Tests render pages through the static manifest, so the runner builds it first.
TEST_RUNNER = "config.test_runner.StaticBuildingRunner"

# Production mail goes through Microsoft Graph (ADR-0011); local development keeps the in-memory sink.
GRAPH_EMAIL_BACKEND = "training.mail.GraphEmailBackend"
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.locmem.EmailBackend" if DEBUG
                          else GRAPH_EMAIL_BACKEND)
DEFAULT_FROM_EMAIL = os.getenv("NOTIFICATION_FROM_EMAIL", "training@example.test")
GRAPH_TENANT_ID = os.getenv("GRAPH_TENANT_ID", "")
GRAPH_CLIENT_ID = os.getenv("GRAPH_CLIENT_ID", "")
GRAPH_CLIENT_SECRET = os.getenv("GRAPH_CLIENT_SECRET", "")
MAIL_TIMEOUT_SECONDS = int(os.getenv("MAIL_TIMEOUT_SECONDS", "15"))
if not DEBUG and EMAIL_BACKEND != GRAPH_EMAIL_BACKEND:
    # Any other backend would quietly drop account emails and notifications in production.
    raise ImproperlyConfigured("EMAIL_BACKEND must be the Graph backend when DJANGO_DEBUG is off")
if EMAIL_BACKEND == GRAPH_EMAIL_BACKEND and not all([GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET]):
    raise ImproperlyConfigured("GRAPH_TENANT_ID, GRAPH_CLIENT_ID and GRAPH_CLIENT_SECRET are required to send email")
# localhost, not 127.0.0.1, because WebAuthn refuses an IP address as the relying party.
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")
if not DEBUG and not APP_BASE_URL.startswith("https://"):
    raise ImproperlyConfigured("APP_BASE_URL must use https when DJANGO_DEBUG is off")
CSRF_TRUSTED_ORIGINS = [APP_BASE_URL] if APP_BASE_URL.startswith("https://") else []
NOTIFICATION_MAX_AUTO_ATTEMPTS = 3
NOTIFICATION_CLAIM_LEASE_SECONDS = 300

# Employees sign in with email and password; see ADR-0009 and docs/plans/29-sign-in-skeleton.md.
AUTH_USER_MODEL = "training.Employee"
AUTHENTICATION_BACKENDS = ["training.accounts.EmployeeBackend"]
# The Lower(email) constraint enforces uniqueness that this check can't see.
SILENCED_SYSTEM_CHECKS = ["auth.W004"]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
     "OPTIONS": {"user_attributes": ("name", "email")}},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "sign_in"
LOGIN_REDIRECT_URL = "home"
# Verification, setup and reset links share this lifetime.
PASSWORD_RESET_TIMEOUT = 24 * 60 * 60
# (limit, window in seconds), counted in RateLimitCounter. Employees in one office can share an address,
# so the per-address sign-in limit is generous. Sign-in failure scopes count only wrong passwords.
ACCOUNT_RATE_LIMITS = {
    "sign_in": (5, 15 * 60), "sign_in_client": (100, 15 * 60), "sign_in_email": (20, 15 * 60),
    "sign_in_device": (10, 15 * 60), "trusted_email": (30, 15 * 60),
    "address_failures": (50, 24 * 60 * 60),
    "passkey_options": (300, 15 * 60), "passkey_failures": (10, 15 * 60), "reauthenticate": (5, 15 * 60),
    "register": (5, 60 * 60), "account_email": (3, 60 * 60), "account_email_target": (10, 60 * 60),
}
# Address blocks and trusted devices; see docs/plans/33-sign-in-attack-protection.md.
ADDRESS_BLOCK_HOURS = 24
TRUSTED_DEVICE_DAYS = 90
# Unconfirmed registrations are removed this long after their newest confirmation email (plan 35).
UNCONFIRMED_REGISTRATION_DAYS = 7

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_AGE = int(os.getenv("SESSION_COOKIE_AGE", str(12 * 60 * 60)))
CSRF_COOKIE_SECURE = not DEBUG
# Trusted only because the application server is reachable through the proxy alone; see docs/plans/30.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = not DEBUG
# The proxy's health probe uses plain HTTP inside the deployment network.
SECURE_REDIRECT_EXEMPT = [r"^health/$"]
# Start small and raise once HTTPS is proven; subdomain and preload flags are left off on purpose.
SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "0" if DEBUG else "3600"))
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
# Requests from these networks may name the client in X-Forwarded-For; set to the reverse proxy's network.
TRUSTED_PROXY_NETWORKS = [ip_network(value.strip()) for value in os.getenv("TRUSTED_PROXY_NETWORKS", "").split(",")
                          if value.strip()]

DEV_AUTH_STUB = os.getenv("DEV_AUTH_STUB", "0") == "1"
DEV_ADMIN_EMAIL = os.getenv("DEV_ADMIN_EMAIL", "demo.admin@example.test")
if DEV_AUTH_STUB and (not DEBUG or any(host not in {"localhost", "127.0.0.1", "[::1]"} for host in ALLOWED_HOSTS)):
    raise ImproperlyConfigured("DEV_AUTH_STUB requires debug mode and loopback-only allowed hosts")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"redact_account_links": {"()": "config.logging.RedactAccountLinks"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "filters": ["redact_account_links"]},
        # Unredacted, for the local account links that stand in for email while the stub is on.
        "local_links": {"class": "logging.StreamHandler"},
    },
    "root": {"handlers": ["console"], "level": os.getenv("LOG_LEVEL", "INFO")},
    "loggers": {
        # Django's own handlers would also print request lines unfiltered.
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.server": {"handlers": ["console"], "level": "INFO", "propagate": False},
        # Local account links go to the server log because the in-memory email sink can't be read.
        "training.accounts": {"handlers": ["local_links" if DEV_AUTH_STUB else "console"],
                              "level": "INFO" if DEV_AUTH_STUB else "WARNING", "propagate": False},
    },
}
