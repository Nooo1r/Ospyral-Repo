from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver
from django.utils import timezone
from django.contrib.auth import get_user_model
from .models import BanRecord
from .utils import kill_user_sessions

User = get_user_model()

@receiver(pre_save, sender=User)
def _audit_ban(sender, instance, **kwargs):
    if not instance.pk:
        return
    try:
        old = sender.objects.get(pk=instance.pk)
    except sender.DoesNotExist:
        return
    if old.is_banned != instance.is_banned:
        action = "BAN" if instance.is_banned else "UNBAN"
        BanRecord.objects.create(
            user=instance,
            action=action,
            reason=getattr(instance, "ban_reason", "") or "",
            until=getattr(instance, "ban_until", None),
        )
        if instance.is_banned:
            kill_user_sessions(instance.id)
    if instance.is_banned and instance.ban_until and instance.ban_until <= timezone.now():
        instance.is_banned = False
        instance.ban_reason = ""
        instance.ban_until = None
