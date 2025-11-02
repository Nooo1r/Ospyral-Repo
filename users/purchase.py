from decimal import Decimal, ROUND_DOWN
from django.utils import timezone
from django.shortcuts import get_object_or_404
from django.http import HttpResponseForbidden
from django.contrib.contenttypes.models import ContentType
from .models import (
    Artwork, ArtworkOrder,
    TextProduct, TextProductOrder,
    CryptoWallet, CryptoTransaction, Currency, Escrow,
)
from datetime import timedelta
from .money import usd_to_micros
from datetime import timedelta
from .crypto.web3 import erc20_balance
from django.conf import settings

def process_purchase(user, product_model, product_id, currency="OSP"):
    """
    Покупка продукта:
      - OSP (оффчейн): списываем баланс OSP, создаём Escrow (HELD), автoрелизом закроется.
      - USDT (ончейн): принудительно используем подключённый primary-кошелёк покупателя (MetaMask, BSC),
                       проверяем on-chain баланс, требуем наличие кошелька у продавца,
                       сохраняем buyer/seller адреса в Escrow. Списания оффчейн-кошелька НЕ делаем.
    """
    # --- 0) Нормализуем валюту (поддержка строк и enum)
    cur = currency
    if isinstance(cur, str):
        cur = cur.upper()
        if cur not in ("OSP", "USDT"):
            return HttpResponseForbidden("Неподдерживаемая валюта")
        cur = getattr(Currency, cur)  # Currency.OSP / Currency.USDT

    # --- 1) Продукт и базовые проверки
    product = get_object_or_404(product_model, pk=product_id)

    if product.owner == user:
        return HttpResponseForbidden("Нельзя купить собственный продукт")

    # уже куплен?
    if product_model == Artwork:
        already = ArtworkOrder.objects.filter(user=user, artwork=product).exists()
    elif product_model == TextProduct:
        already = TextProductOrder.objects.filter(user=user, product=product).exists()
    else:
        return HttpResponseForbidden("Неподдерживаемый тип продукта")

    if already:
        return None  # уже куплен, возвращаем None как и раньше

    # --- 2) Цена (в USD-эквиваленте). Для USDT принимаем 1 USDT ~= 1 USD.
    try:
        price = Decimal(product.price)
    except Exception:
        return HttpResponseForbidden("Цена продукта указана некорректно")

    if price <= 0:
        return HttpResponseForbidden("Недопустимая цена продукта")

    # --- 3) Ветвление по валюте
    buyer_wallet = None
    seller_wallet = None
    need_crypto_log = False   # лог в CryptoTransaction делаем только для OSP

    if cur == Currency.OSP:
        # 3A) Оффчейн-ветка: списание OSP из внутреннего кошелька пользователя
        try:
            buyer_wallet = CryptoWallet.objects.get(user=user, currency=Currency.OSP)
        except CryptoWallet.DoesNotExist:
            return HttpResponseForbidden("Кошелёк OSP не найден")

        # Проверяем оба поля, чтобы не был нарушен микробаланс
        if buyer_wallet.balance < price:
            return HttpResponseForbidden("Недостаточно средств (OSP)")
        micros_delta = int(usd_to_micros(price))
        if buyer_wallet.balance_micros < micros_delta:
            return HttpResponseForbidden("Недостаточно средств (OSP микробаланс)")

        # Списание оффчейн
        buyer_wallet.balance -= price
        buyer_wallet.balance_micros -= micros_delta
        buyer_wallet.save(update_fields=["balance", "balance_micros"])

        need_crypto_log = True

    elif cur == Currency.USDT:
        # 3B) Ончейн-ветка: проверяем primary внешний кошелёк покупателя и наличие кошелька у продавца
        usdt_addr = getattr(settings, "USDT_TOKEN_ADDRESS", None)
        usdt_dec  = int(getattr(settings, "USDT_DECIMALS", 6))
        if not usdt_addr:
            return HttpResponseForbidden("USDT не сконфигурирован")

        # Покупатель должен иметь внешний, primary, подтверждённый кошелёк USDT
        buyer_wallet = (CryptoWallet.objects
                        .filter(user=user, currency=Currency.USDT, is_external=True,
                                is_primary=True, verified_at__isnull=False)
                        .first())
        if not buyer_wallet or not buyer_wallet.address:
            return HttpResponseForbidden("Подключите и подтвердите USDT-кошелёк (MetaMask)")

        # Продавец должен иметь внешний кошелёк USDT (иначе релизить будет некуда)
        seller_wallet = (CryptoWallet.objects
                         .filter(user=product.owner, currency=Currency.USDT, is_external=True)
                         .order_by('-is_primary', '-verified_at')
                         .first())
        if not seller_wallet or not seller_wallet.address:
            return HttpResponseForbidden("Продавец пока не подключил USDT-кошелёк")

        # Проверка on-chain баланса покупателя: требуется >= price * 10**decimals
        # NB: price в USD ~= USDT.
        def _to_base_units(amount_dec: Decimal, decimals: int) -> int:
            q = Decimal(10) ** decimals
            return int((amount_dec * q).to_integral_value(rounding=ROUND_DOWN))

        try:
            bal = erc20_balance(usdt_addr, buyer_wallet.address)
        except Exception:
            return HttpResponseForbidden("Не удалось получить баланс USDT-кошелька")

        needed = _to_base_units(price, usdt_dec)
        if int(bal) < int(needed):
            return HttpResponseForbidden("Недостаточно средств на USDT-кошельке")

        # ВНИМАНИЕ: в USDT-ветке НИЧЕГО не списываем оффчейн — спишет смарт-контракт при депозите.

    else:
        return HttpResponseForbidden("Неподдерживаемая валюта")

    # --- 4) Создание заказа (как у тебя и было)
    seller = product.owner
    if product_model == Artwork:
        # В ArtworkOrder нет поля 'price'; используем amount (целая база)
        order = ArtworkOrder.objects.create(
            user=user,
            artwork=product,
            amount=Decimal(int(price)),  # оставляю твою логику (int); при необходимости меняется базовая единица
        )
        order_ct = ContentType.objects.get_for_model(ArtworkOrder)
    else:
        order = TextProductOrder.objects.create(
            user=user,
            product=product,
            price=price,
            is_active=True,
        )
        order_ct = ContentType.objects.get_for_model(TextProductOrder)

    # --- 5) (только для OSP) внутренний лог escrow-транзакции
    if cur == Currency.OSP and need_crypto_log and buyer_wallet:
        CryptoTransaction.objects.create(
            wallet=buyer_wallet,
            tx_type='escrow',
            amount=price,
            amount_micros=int(usd_to_micros(price)),
            reference=f'escrow_order_{order.id}',
        )

    # --- 6) Создаём Escrow (HELD) с синхронизацией on-chain адресов для USDT
    esc_defaults = dict(
        amount_micros=int(usd_to_micros(price)),
        buyer=user,
        seller=seller,
        status=Escrow.Status.HELD,
        auto_release_at=timezone.now() + timedelta(hours=48),
        idempotency_key=f"{'art' if product_model == Artwork else 'text'}:{order.id}",
    )

    if cur == Currency.USDT:
        esc_defaults.update({
            "buyer_address":  buyer_wallet.address if buyer_wallet else None,
            "seller_address": seller_wallet.address if seller_wallet else None,
        })

    Escrow.objects.get_or_create(
        order_ct=order_ct,
        order_id=order.id,
        defaults=esc_defaults,
    )

    # Для OSP релиз обработает авто-джоб; для USDT — события из sync_escrow_events.
    return order