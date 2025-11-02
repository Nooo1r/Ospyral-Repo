
from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings
from django.contrib.auth import get_user_model
from .models import CryptoWallet, LedgerEntry, CryptoTransaction, Currency
from .accounting import book_osp
from celery import shared_task

MICROS = Decimal("1000000")
Q6 = Decimal("0.000001")
from django.db import transaction

def usd_to_micros(amount) -> int:
    """
    Преобразует сумму в USD/USDC/OSP в микродоллары (целое число).
    Поддерживает Decimal/str/float/int на входе.
    """
    d = Decimal(str(amount))
    return int((d * MICROS).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

def micros_to_usd(micros: int) -> Decimal:
    """
    Возвращает Decimal с 6 знаками после запятой.
    """
    return (Decimal(int(micros)) / MICROS).quantize(Q6)

def add_usd_to_micros(micros: int, delta_usd) -> int:
    """Удобный хелпер для инкремента целого микробаланса на долларовую сумму."""
    return micros + usd_to_micros(delta_usd)

def get_platform_osp_wallet():
    User = get_user_model()
    platform_user = User.objects.get(pk=int(getattr(settings, "PLATFORM_OSP_WALLET_USER_ID")))
    return CryptoWallet.objects.get(user=platform_user, currency=Currency.OSP)

def calc_fee_micros(amount_micros: int) -> int:
    # комиссия в б.п.: 1000 = 10%
    bps = getattr(settings, "PLATFORM_FEE_BPS", 1000)
    return (amount_micros * bps) // 10000



@shared_task(bind=True, max_retries=0)
def settle_osp_release_with_fee(self, seller_id: int, total_amount_micros: int, escrow_id: int, order_ref: str):
    """
    Разнесение релиза OSP-эскроу: комиссия платформе + нетто продавцу.
    Вызывается из auto_release_held_escrows ПОСЛЕ того как escrow.status=RELEASED зафиксирован в БД.
    Идемпотентность:
      - разный reference для fee и net: <base_ref>:fee и <base_ref>:net
      - CryptoTransaction через get_or_create
      - Ledger записываем с теми же уникальными reference
    """
    with transaction.atomic():
        # блокировки кошельков
        seller_wallet = (CryptoWallet.objects
                         .select_for_update()
                         .get(user_id=seller_id, currency=Currency.OSP))
        platform_wallet = get_platform_osp_wallet()  # твой хелпер

        total = int(total_amount_micros)
        if total <= 0:
            raise ValueError("Total amount must be positive")

        fee = calc_fee_micros(total)  # твой хелпер комиссии
        net = total - fee
        if net < 0:
            raise ValueError("Net amount negative")

        base_ref = f"escrow:{escrow_id}|{order_ref}"
        fee_ref = f"{base_ref}:fee"
        net_ref = f"{base_ref}:net"

        # ---------- 1) ПЛАТФОРМА: комиссия ----------
        book_osp(
            platform_wallet,
            kind=LedgerEntry.Kind.SALE_FEE,
            reference=fee_ref,
            delta_micros=+fee,
        )
        CryptoTransaction.objects.get_or_create(
            wallet=platform_wallet,
            tx_type="sale_fee_income",          # было 'fee_income' — можно оставить старое, если так уже в аналитике
            reference=fee_ref,
            defaults={
                "amount_micros": fee,
                "amount": micros_to_usd(fee) if "micros_to_usd" in globals() else (Decimal(fee) / Decimal(1_000_000)),
                "tx_hash": "",
            }
        )

        # ---------- 2) ПРОДАВЕЦ: нетто ----------
        book_osp(
            seller_wallet,
            kind=LedgerEntry.Kind.SALE_INCOME,
            reference=net_ref,
            delta_micros=+net,
        )
        CryptoTransaction.objects.get_or_create(
            wallet=seller_wallet,
            tx_type="sale_income",
            reference=net_ref,
            defaults={
                "amount_micros": net,
                "amount": micros_to_usd(net) if "micros_to_usd" in globals() else (Decimal(net) / Decimal(1_000_000)),
                "tx_hash": "",
            }
        )

    return {"fee_micros": fee, "net_micros": net}


def settle_vip_revenue_to_platform(amount_micros: int, buyer_wallet: CryptoWallet, plan_code: str):
    """100% оплаты VIP → кошелёк платформы"""
    platform_wallet = get_platform_osp_wallet()
    # покупатель уже списан в buy_vip_view; здесь — зачисляем платформе
    book_osp(
        platform_wallet,
        kind=LedgerEntry.Kind.PURCHASE_VIP,
        reference=f"vip:{plan_code}",
        delta_micros=+amount_micros,
    )
    CryptoTransaction.objects.create(
        wallet=platform_wallet,
        tx_type="vip_income",
        amount=Decimal(amount_micros) / Decimal("1000000"),
        amount_micros=amount_micros,
        reference=f"vip:{plan_code}",
    )


def settle_sale_with_platform_fee_osp(total_amount_micros: int, seller_wallet: CryptoWallet, order_ref: str):
    """Продажа за OSP: 10% платформе, 90% продавцу (сумму принимает escrow.release)"""
    fee = calc_fee_micros(total_amount_micros)
    net = total_amount_micros - fee
    if net < 0:
        raise ValueError("Net amount negative")

    # 1) Платформа получает комиссию
    platform_wallet = get_platform_osp_wallet()
    book_osp(
        platform_wallet,
        kind=LedgerEntry.Kind.SALE_FEE,
        reference=f"sale:{order_ref}",
        delta_micros=+fee,
    )
    CryptoTransaction.objects.create(
        wallet=platform_wallet,
        tx_type="fee_income",
        amount=Decimal(fee) / Decimal("1000000"),
        amount_micros=fee,
        reference=f"sale:{order_ref}",
    )

    # 2) Продавец получает нетто
    book_osp(
        seller_wallet,
        kind=LedgerEntry.Kind.SALE_INCOME,
        reference=f"sale:{order_ref}",
        delta_micros=+net,
    )
    CryptoTransaction.objects.create(
        wallet=seller_wallet,
        tx_type="sale_income",
        amount=Decimal(net) / Decimal("1000000"),
        amount_micros=net,
        reference=f"sale:{order_ref}",
    )

    return fee, net