import os
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

ALLOWED_HOSTS = [host.strip() for host in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if host.strip()]
if os.getenv("RENDER_EXTERNAL_HOSTNAME"):
    ALLOWED_HOSTS.append(os.environ["RENDER_EXTERNAL_HOSTNAME"])

INSTALLED_APPS = [
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
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request", "django.template.context_processors.csrf"]},
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

# The notification worker uses a local-only sink until the mail integration is approved.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
DEFAULT_FROM_EMAIL = os.getenv("NOTIFICATION_FROM_EMAIL", "training@example.test")
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
NOTIFICATION_MAX_AUTO_ATTEMPTS = 3
NOTIFICATION_CLAIM_LEASE_SECONDS = 300

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = not DEBUG

DEV_AUTH_STUB = os.getenv("DEV_AUTH_STUB", "0") == "1"
DEV_ADMIN_EMAIL = os.getenv("DEV_ADMIN_EMAIL", "demo.admin@example.test")
if DEV_AUTH_STUB and (not DEBUG or any(host not in {"localhost", "127.0.0.1", "[::1]"} for host in ALLOWED_HOSTS)):
    raise ImproperlyConfigured("DEV_AUTH_STUB requires debug mode and loopback-only allowed hosts")
