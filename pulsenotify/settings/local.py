"""Local development settings."""
import os
import sys

from .base import *  # noqa: F401,F403

DEBUG = True

ALLOWED_HOSTS = ['localhost', '127.0.0.1']

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('DB_NAME', 'pulsenotify'),
        'USER': os.environ.get('DB_USER', 'postgres'),
        'PASSWORD': os.environ.get('DB_PASSWORD', 'postgres'),
        'HOST': os.environ.get('DB_HOST', 'localhost'),
        'PORT': os.environ.get('DB_PORT', '5432'),
    }
}

# On macOS, billiard (Celery's prefork pool) starts workers with "spawn",
# which breaks the prefork pool. Use the threads pool for local development.
if sys.platform == 'darwin':
    CELERY_WORKER_POOL = 'threads'
