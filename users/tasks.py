from celery import shared_task
from django.core.management import call_command
from django.utils import timezone
from django.contrib.auth import get_user_model

@shared_task
def purge_bans_task():
    call_command('purge_bans')

@shared_task
def expire_vips_task():
    User = get_user_model()
    now = timezone.now()
    qs = User.objects.filter(profile__vip_plan__isnull=False, profile__vip_expires_at__isnull=False, profile__vip_expires_at__lte=now).select_related('profile')
    updated = 0
    for u in qs:
        p = u.profile
        p.vip_plan = None
        p.vip_expires_at = None
        p.save(update_fields=["vip_plan", "vip_expires_at"])
        updated += 1
    return updated