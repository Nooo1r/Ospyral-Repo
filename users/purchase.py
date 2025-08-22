from decimal import Decimal
from django.utils import timezone
from django.shortcuts import get_object_or_404
from django.http import HttpResponseForbidden
from .models import (
    Artwork, ArtworkOrder,
    TextProduct, TextProductOrder,
    CryptoWallet, CryptoTransaction, Currency,
    RefundRequest
)
from .crypto.tasks import release_escrow
from datetime import timedelta

def process_purchase(user, product_model, product_id, currency="OSP"):
    product = get_object_or_404(product_model, pk=product_id)

    if product.owner == user:
        return HttpResponseForbidden("Нельзя купить собственный продукт")

    # Проверка: уже куплен?
    if product_model == Artwork:
        exists = ArtworkOrder.objects.filter(user=user, artwork=product).exists()
    elif product_model == TextProduct:
        exists = TextProductOrder.objects.filter(user=user, product=product).exists()
    else:
        return HttpResponseForbidden("Неподдерживаемый тип продукта")

    if exists:
        return None  # уже куплен

    # Проверка кошелька
    try:
        wallet = CryptoWallet.objects.get(user=user, currency=currency)
    except CryptoWallet.DoesNotExist:
        return HttpResponseForbidden("Кошелёк не найден")

    price = Decimal(product.price)

    if wallet.balance < price:
        return HttpResponseForbidden("Недостаточно средств")

    # Списание средств (временное удержание)
    wallet.balance -= price
    wallet.save()

    # Создаём заказ и escrow
    if product_model == Artwork:
        order = ArtworkOrder.objects.create(
            user=user,
            artwork=product,
            price=price
        )
    elif product_model == TextProduct:
        order = TextProductOrder.objects.create(
            user=user,
            product=product,
            price=price
        )

    # Логируем escrow транзакцию
    CryptoTransaction.objects.create(
        wallet=wallet,
        tx_type='escrow',
        amount=price,
        reference=f'escrow_order_{order.id}'
    )

    # Запускаем отложенный релиз через 48 часов
    release_escrow.apply_async((order.id,), eta=timezone.now() + timedelta(hours=48))

    return order
