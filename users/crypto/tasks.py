import logging
from celery import shared_task
from django.conf import settings
from django.db import transaction
from decimal import Decimal, ROUND_HALF_UP

from .web3 import w3, escrow_contract  # usdc_contract не используется здесь — не импортируем
from ..models import ChainCursor, ArtworkOrder, TextProductOrder, CryptoWallet, Currency

logger = logging.getLogger(__name__)
BATCH_SIZE = 3000  # читаем логи батчами (под Sepolia можно и больше, но 3k безопасно)



@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def sync_escrow_events(self):
    from_block = None
    to_block = None
    try:
        # курсор
        escrow_addr = w3.to_checksum_address(settings.ESCROW_CONTRACT_ADDRESS)
        cursor, _ = ChainCursor.objects.get_or_create(
            network=ChainCursor.Network.ETHEREUM_SEPOLIA,
            contract=escrow_addr,
            defaults={"last_block": max(0, w3.eth.block_number - 1000), "note": "init"},
        )

        head = w3.eth.block_number
        start = cursor.last_block + 1
        if head < start:
            return

        # батчами
        cur = start
        while cur <= head:
            end = min(cur + BATCH_SIZE - 1, head)

            # Если нужны Created — можно обработать, но без надёжного сопоставления лучше пропустить
            # created_logs = escrow_contract.events.OrderCreated().get_logs(fromBlock=cur, toBlock=end)

            deposited_logs = escrow_contract.events.OrderDeposited().get_logs(fromBlock=cur, toBlock=end)
            for ev in deposited_logs:
                oid = ev["args"]["orderId"]
                txh = ev["transactionHash"].hex()
                # Artwork
                ArtworkOrder.objects.filter(external_order_id=oid).update(
                    onchain_status="DEPOSITED",
                    deposit_tx=txh,
                    status=ArtworkOrder.Status.PENDING,
                )
                # Если есть заказы на тексты — раскомментируй:
                TextProductOrder.objects.filter(external_order_id=oid).update(
                    onchain_status="DEPOSITED",
                    deposit_tx=txh,
                )

            released_logs = escrow_contract.events.OrderReleased().get_logs(fromBlock=cur, toBlock=end)
            for ev in released_logs:
                oid = ev["args"]["orderId"]
                txh = ev["transactionHash"].hex()
                ArtworkOrder.objects.filter(external_order_id=oid).update(
                    onchain_status="RELEASED",
                    release_tx=txh,
                    status=ArtworkOrder.Status.RELEASED,
                )
                TextProductOrder.objects.filter(external_order_id=oid).update(
                    onchain_status="RELEASED",
                    release_tx=txh,
                )

            refunded_logs = escrow_contract.events.OrderRefunded().get_logs(fromBlock=cur, toBlock=end)
            for ev in refunded_logs:
                oid = ev["args"]["orderId"]
                txh = ev["transactionHash"].hex()
                ArtworkOrder.objects.filter(external_order_id=oid).update(
                    onchain_status="REFUNDED",
                    refund_tx=txh,
                    status=ArtworkOrder.Status.REFUNDED,
                )
                TextProductOrder.objects.filter(external_order_id=oid).update(
                    onchain_status="REFUNDED",
                    refund_tx=txh,
                )

            # (опц.) TIMEOUT, если такое событие есть в ABI
            if hasattr(escrow_contract.events, "OrderTimeout"):
                timeout_logs = escrow_contract.events.OrderTimeout().get_logs(fromBlock=cur, toBlock=end)
                for ev in timeout_logs:
                    oid = ev["args"]["orderId"]
                    ArtworkOrder.objects.filter(external_order_id=oid).update(
                        onchain_status="TIMEOUT",
                        status=ArtworkOrder.Status.COMPLETED,
                    )
                    TextProductOrder.objects.filter(external_order_id=oid).update(
                        onchain_status="TIMEOUT",
                    )

            # продвигаем курсор
            with transaction.atomic():
                cursor.last_block = end
                cursor.save(update_fields=["last_block"])

            cur = end + 1

    except Exception as exc:
        logger.error(f"[sync_escrow_events] range={from_block}-{to_block} err={exc}", exc_info=True)
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=0)
def mint_osp_for_user(self, user_id: int, amount: int | str | float):
    """
    Универсальная задача для изменения внутреннего баланса OSP.
    amount может быть отрицательным (списание) или положительным (зачисление).
    Интерпретируем amount как «целые OSP» (как это делается в вызовах из views).
    """
    try:
        # Нормализуем во Decimal (целые токены по текущему коду вызова)
        delta = Decimal(str(amount)).quantize(Decimal('1'), rounding=ROUND_HALF_UP)

        with transaction.atomic():
            wallet = (CryptoWallet.objects
                      .select_for_update()
                      .get(user_id=user_id, currency=Currency.OSP))
            wallet.balance = (wallet.balance or Decimal('0')) + delta
            wallet.save(update_fields=['balance'])

        logger.info(f"[mint_osp_for_user] user={user_id} delta={delta} new_balance={wallet.balance}")
        return {"user_id": user_id, "delta": str(delta)}

    except CryptoWallet.DoesNotExist:
        logger.error(f"[mint_osp_for_user] OSP-кошелек не найден для user_id={user_id}")
        # Не ретраим: это логическая ошибка конфигурации
        return {"error": "wallet_not_found", "user_id": user_id}
    except Exception as e:
        logger.exception(f"[mint_osp_for_user] unexpected error: {e}")
        raise