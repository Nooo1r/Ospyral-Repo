from django.core.management.base import BaseCommand
from django.utils import timezone
from django.contrib.auth import get_user_model
from ...models import BannedFingerprint

class Command(BaseCommand):
    help = "Удаляет просроченные отпечатки и авто-снимает баны по истёкшему сроку"

    def handle(self, *args, **opts):
        now = timezone.now()

        n = BannedFingerprint.objects.filter(expires_at__isnull=False, expires_at__lte=now).delete()[0]
        self.stdout.write(self.style.SUCCESS(f"Expired fingerprints removed: {n}"))

        User = get_user_model()
        updated = 0
        for u in User.objects.filter(is_banned=True, ban_until__isnull=False, ban_until__lte=now):
            u.is_banned = False
            u.ban_reason = ""
            u.ban_until = None
            u.save(update_fields=["is_banned","ban_reason","ban_until"])
            updated += 1
        self.stdout.write(self.style.SUCCESS(f"Auto-unbanned users: {updated}"))
