
from django.contrib.auth.models import AnonymousUser
from django.urls import reverse
from django.conf import settings

def unread_notifications(request):
    user = getattr(request, "user", AnonymousUser())
    count = 0
    if getattr(user, "is_authenticated", False):
        count = 0
    return {"unread_notifications": count}

def wallet_summary(request):
    data = {
        "sidebar_osp_balance": None,
        "wallet_deposit_url": reverse("wallets") + "?tab=deposit",
    }
    u = getattr(request, "user", None)
    if not u or not u.is_authenticated:
        return data

    try:
        w = u.crypto_wallets.get(currency="OSP")
        data["sidebar_osp_balance"] = w.balance  # Decimal уже форматируется в шаблоне
    except Exception:
        pass
    return data

def config_warnings(request):
    warnings = []

    required = [
        ("USDT_TOKEN_ADDRESS", getattr(settings, "USDT_TOKEN_ADDRESS", "")),
        ("USDT_DECIMALS", getattr(settings, "USDT_DECIMALS", "")),
        ("ESCROW_CONTRACT_ADDRESS", getattr(settings, "ESCROW_CONTRACT_ADDRESS", "")),
    ]
    for key, val in required:
        if not val:
            warnings.append(f"Отсутствует настройка {key} — ончейн-логика может не работать.")

    return {"ADMIN_CONFIG_WARNINGS": warnings}