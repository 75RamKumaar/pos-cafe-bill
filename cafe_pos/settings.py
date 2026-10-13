from pathlib import Path

from decouple import Csv, config
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = config("DJANGO_DEBUG", default=False, cast=bool)
SECRET_KEY = config("DJANGO_SECRET_KEY", default="")

if not DEBUG:
    if SECRET_KEY.startswith("replace-with") or len(SECRET_KEY) < 32:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY must be at least 32 characters and cannot start with 'replace-with' when DEBUG is False."
        )

ALLOWED_HOSTS = config(
    "DJANGO_ALLOWED_HOSTS",
    default="127.0.0.1,localhost",
    cast=Csv(),
)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "pos",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "cafe_pos.urls"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {
        "context_processors": [
            "django.template.context_processors.request",
            "django.contrib.auth.context_processors.auth",
            "django.contrib.messages.context_processors.messages",
            "django.template.context_processors.i18n",
            "pos.context_processors.roles",
        ],
        "builtins": [
            "pos.templatetags.pos_tags",
            "django.templatetags.i18n",
        ],
    },
}]

WSGI_APPLICATION = "cafe_pos.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "OPTIONS": {
            "transaction_mode": "IMMEDIATE",
        },
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "en"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

LANGUAGES = [
    ("en", "English"),
    ("ta", "Tamil"),
]

LOCALE_PATHS = [
    BASE_DIR / "locale",
]

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}

def whitenoise_custom_headers(headers, path, url):
    if "sw.js" in url:
        headers["Cache-Control"] = "no-cache, no-store, must-revalidate"

def whitenoise_immutable_test(path, url):
    if "sw.js" in url:
        return False
    import re
    return bool(re.search(r"\.[0-9a-f]{12}\.", url))

WHITENOISE_ADD_HEADERS_FUNCTION = whitenoise_custom_headers
WHITENOISE_IMMUTABLE_FILE_TEST = whitenoise_immutable_test


MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = 12 * 60 * 60  # 12 hours
SESSION_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "cafe-pos-cache",
    }
}

DJANGO_HTTPS = config("DJANGO_HTTPS", default=False, cast=bool)
if DJANGO_HTTPS:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "billing"
LOGOUT_REDIRECT_URL = "login"
