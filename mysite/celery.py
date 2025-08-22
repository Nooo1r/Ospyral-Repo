import os
from celery import Celery
from celery.schedules import crontab
import ssl
from django.conf import settings

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mysite.settings')

app = Celery('mysite')

app.conf.broker_pool_limit = 10       
app.conf.broker_connection_max_retries = 3
app.conf.broker_connection_retry_on_startup = True

app.config_from_object('django.conf:settings', namespace='CELERY')
app.conf.task_ignore_result = True

app.autodiscover_tasks(lambda: settings.INSTALLED_APPS)

app.conf.beat_schedule = {
    'release-escrows-daily': {
        'task': 'users.crypto.tasks.release_all_pending_escrows',
        'schedule': crontab(minute=0, hour=0),
    },
} 

app.conf.beat_schedule.update({
    'check-osp-deposits-every-5-minutes': {
        'task': 'users.tasks.check_osp_deposits',
        'schedule': 300.0,
    },
})