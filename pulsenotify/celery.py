import os
from pathlib import Path

from celery import Celery
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / '.env')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pulsenotify.settings.local')

app = Celery('pulsenotify')

# Read every CELERY_* key from Django settings (CELERY_BROKER_URL, CELERY_BEAT_SCHEDULE, ...)
app.config_from_object('django.conf:settings', namespace='CELERY')

# Discover tasks.py in every installed app
app.autodiscover_tasks()
