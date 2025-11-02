from django.core.management.base import BaseCommand
from django.db import transaction
from ...models import VIPPlan

def mu(n: int) -> int:
    """Преобразует OSP в микросы (μOSP). 1 OSP = 1_000_000 μOSP."""
    return int(n) * 1_000_000

PLANS = [
    # GOLD
    dict(code="GOLD_7",    title="Gold VIP — 7 дней",    level="GOLD",   duration_days=7,   price_osp_micros=mu(7),   popularity_boost=40000,  daily_quota=0, min_interval_days=2, is_active=True),
    dict(code="GOLD_30",   title="Gold VIP — 30 дней",   level="GOLD",   duration_days=30,  price_osp_micros=mu(20),  popularity_boost=40000,  daily_quota=0, min_interval_days=2, is_active=True),
    dict(code="GOLD_180",  title="Gold VIP — 180 дней",  level="GOLD",   duration_days=180, price_osp_micros=mu(90),  popularity_boost=40000,  daily_quota=0, min_interval_days=2, is_active=True),
    dict(code="GOLD_365",  title="Gold VIP — 365 дней",  level="GOLD",   duration_days=365, price_osp_micros=mu(150), popularity_boost=40000,  daily_quota=0, min_interval_days=2, is_active=True),

    # STAR
    dict(code="STAR_7",    title="Star VIP — 7 дней",    level="STAR",   duration_days=7,   price_osp_micros=mu(12),  popularity_boost=100000, daily_quota=1, min_interval_days=0, is_active=True),
    dict(code="STAR_30",   title="Star VIP — 30 дней",   level="STAR",   duration_days=30,  price_osp_micros=mu(35),  popularity_boost=100000, daily_quota=1, min_interval_days=0, is_active=True),
    dict(code="STAR_180",  title="Star VIP — 180 дней",  level="STAR",   duration_days=180, price_osp_micros=mu(150), popularity_boost=100000, daily_quota=1, min_interval_days=0, is_active=True),
    dict(code="STAR_365",  title="Star VIP — 365 дней",  level="STAR",   duration_days=365, price_osp_micros=mu(245), popularity_boost=100000, daily_quota=1, min_interval_days=0, is_active=True),

    # GALAXY
    dict(code="GALAXY_7",   title="Galaxy VIP — 7 дней",   level="GALAXY", duration_days=7,   price_osp_micros=mu(20),  popularity_boost=250000, daily_quota=2, min_interval_days=0, is_active=True),
    dict(code="GALAXY_30",  title="Galaxy VIP — 30 дней",  level="GALAXY", duration_days=30,  price_osp_micros=mu(60),  popularity_boost=250000, daily_quota=2, min_interval_days=0, is_active=True),
    dict(code="GALAXY_180", title="Galaxy VIP — 180 дней", level="GALAXY", duration_days=180, price_osp_micros=mu(245), popularity_boost=250000, daily_quota=2, min_interval_days=0, is_active=True),
    dict(code="GALAXY_365", title="Galaxy VIP — 365 дней", level="GALAXY", duration_days=365, price_osp_micros=mu(400), popularity_boost=250000, daily_quota=2, min_interval_days=0, is_active=True),
]

class Command(BaseCommand):
    help = "Seed/обновление VIP планов (идемпотентно)."

    @transaction.atomic
    def handle(self, *args, **options):
        created, updated = 0, 0
        for data in PLANS:
            code = data["code"]
            obj, is_created = VIPPlan.objects.update_or_create(code=code, defaults=data)
            created += int(is_created)
            updated += int(not is_created)
        self.stdout.write(self.style.SUCCESS(f"VIP plans seeded. Created: {created}, Updated: {updated}"))

        # (опционально) деактивировать планы, которых уже нет в PLANS
        known_codes = {p["code"] for p in PLANS}
        deactivated = VIPPlan.objects.exclude(code__in=known_codes).update(is_active=False)
        if deactivated:
            self.stdout.write(self.style.WARNING(f"Deactivated stale plans: {deactivated}"))