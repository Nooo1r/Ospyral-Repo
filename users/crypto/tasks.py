import logging
from celery import shared_task
from django.conf import settings
from django.db import transaction
from decimal import Decimal
from django.utils import timezone
from django.contrib.contenttypes.models import ContentType
from .web3 import w3, escrow_contract, build_release_tx
import random
import time
from requests.exceptions import HTTPError
from ..utils import send_tx                    
from .web3 import w3, escrow_contract, build_release_tx, get_escrow_address_checksum, Web3 
from decimal import Decimal, ROUND_DOWN
from ..money import micros_to_usd, MICROS

from ..models import(
    ChainCursor, CryptoTransaction,
    Escrow, ArtworkOrder, 
    TextProductOrder, 
    CryptoWallet, Purchase,
    Currency, Artwork)

logger = logging.getLogger(__name__)
BATCH_SIZE = 3000  # читаем логи батчами (под Sepolia можно и больше, но 3k безопасно)
BATCH_LIMIT = 200
LOGS_BLOCK_CHUNK = 500
RATE_LIMIT_BACKOFF_BASE = 1.0
RATE_LIMIT_BACKOFF_MAX = 20.0


CHAIN_ID_EXPECTED   = int(getattr(settings, "CHAIN_ID", 0))
EVENT_CONFIRMATIONS = getattr(settings, "EVENT_CONFIRMATIONS", 3)
USDT_DECIMALS       = int(getattr(settings, "USDT_DECIMALS", 6))

ESCROW_ADDR = get_escrow_address_checksum()
USDT_ADDR = None
try:
    _usdt = getattr(settings, "USDT_TOKEN_ADDRESS", "") or ""
    USDT_ADDR = Web3.to_checksum_address(_usdt) if _usdt and len(_usdt) >= 42 else None
except Exception:
    USDT_ADDR = None


# Имена событий в ABI (переименуй, если у тебя другие):
EV_CREATED = "OrderCreated"   # (bytes32 orderId, address token, uint256 amount, address buyer, address seller)
EV_RELEASE = "OrderReleased"  # (bytes32 orderId, address by, ...)
EV_REFUND  = "OrderRefunded"  # (bytes32 orderId, address by, ...)


def _to_base_units(amount_dec: Decimal, decimals: int) -> int:
    q = Decimal(10) ** decimals
    return int((amount_dec * q).to_integral_value(rounding=ROUND_DOWN))

def _chain_ok() -> bool:
    try:
        net = w3.eth.chain_id
        return int(net) == int(getattr(settings, "CHAIN_ID", 0))
    except Exception as ex:
        print(f"[sync] chain id check failed: {ex}")
        return False

def _amount_expected_for_escrow(e: Escrow) -> int:
    """
    Возвращаем ожидаемую сумму в base units для сравнения с ончейн-amount.
    Для USDT decimals=6, у тебя в Escrow хранится amount_micros — это как раз 10^6.
    """
    return int(e.amount_micros)  # при USDT это уже base units


def _get_logs(ev_cls, start_block: int, end_block: int):
    """
    Унификация Web3: в v6 параметры snake_case (from_block), в v5 — camelCase (fromBlock).
    """
    try:
        # web3.py v6
        return ev_cls().get_logs(from_block=start_block, to_block=end_block)
    except TypeError:
        # web3.py v5
        return ev_cls().get_logs(fromBlock=start_block, toBlock=end_block)


def _get_logs_once(ev_cls, start_block: int, end_block: int):
    """Один вызов get_logs с поддержкой web3 v5/v6 параметров."""
    try:
        return ev_cls().get_logs(from_block=start_block, to_block=end_block)  # v6
    except TypeError:
        return ev_cls().get_logs(fromBlock=start_block, toBlock=end_block)    # v5

def _fetch_logs_resilient(ev_cls, start_block: int, end_block: int):
    """
    Забираем логи маленькими чанками по LOGS_BLOCK_CHUNK c бэкоффом на 429.
    Возвращаем генератор событий.
    """
    current = start_block
    while current <= end_block:
        chunk_end = min(current + LOGS_BLOCK_CHUNK - 1, end_block)
        attempt = 0
        while True:
            try:
                for ev in _get_logs_once(ev_cls, current, chunk_end):
                    yield ev
                break  # чанк успешно отработан
            except HTTPError as e:
                # у Infura 429 — rate limit
                if e.response is not None and e.response.status_code == 429:
                    # экспоненциальный бэкофф с джиттером
                    sleep_s = min(RATE_LIMIT_BACKOFF_BASE * (2 ** attempt), RATE_LIMIT_BACKOFF_MAX)
                    sleep_s = sleep_s * (0.7 + 0.6 * random.random())  # +/- джиттер
                    time.sleep(sleep_s)
                    attempt += 1
                    continue
                # другие HTTP-ошибки — пробрасываем
                raise
        current = chunk_end + 1



@shared_task(bind=True, max_retries=3, default_retry_delay=60, acks_late=True)
def sync_escrow_events(self):
    """
    Обработка событий контракта с защитами:
    - проверяем chainId и адрес контракта,
    - читаем только подтверждённые блоки,
    - валидируем token/amount/orderId,
    - идемпотентность по txHash,
    - корректные переходы статусов + снятие флагов спора.
    """
    # 0) chain id
    try:
        chain_id = int(w3.eth.chain_id)
        if chain_id != CHAIN_ID_EXPECTED:
            logger.warning(f"[sync_escrow_events] wrong chain id {chain_id} != {CHAIN_ID_EXPECTED}, skip")
            return {"status": "wrong_chain"}
    except Exception as ex:
        logger.error(f"[sync_escrow_events] chain id check failed: {ex}", exc_info=True)
        return {"status": "chain_check_error"}

    # безопасно берём адрес эскроу
    escrow_addr = get_escrow_address_checksum()   # <-- используем helper из .web3
    if not escrow_addr:
        logger.warning("[sync_escrow_events] escrow address not configured, skip")
        return {"status": "no_escrow_address"}

    head = w3.eth.block_number
    if head is None:
        return {"status": "no_head"}

    # Читаем с лагом по подтверждениям
    to_block = max(0, head - EVENT_CONFIRMATIONS)

    # Курсор (оставляем твою модель)
    cursor, _ = ChainCursor.objects.get_or_create(
        network=str(getattr(settings, "CHAIN_ID", 0)), 
        contract=escrow_addr,
        defaults={"last_block": max(0, to_block - 1000), "note": "init"},
    )
    start = cursor.last_block + 1
    if to_block < start:
        return {"status": "up_to_date", "from": start, "to": to_block}

    art_ct  = ContentType.objects.get_for_model(ArtworkOrder)
    text_ct = ContentType.objects.get_for_model(TextProductOrder)

    applied = {"created": 0, "released": 0, "refunded": 0, "skipped": 0}
    cur = start

    # Функция извлечения логов с фильтрацией по адресу контракта
    def _fetch(ev_name, frm, to):
        ev = getattr(escrow_contract.events, ev_name, None)
        if not ev:
            return []
        try:
            # адрес явно фиксируем
            return _fetch_logs_resilient(ev, frm, to, address=ESCROW_ADDR)
        except TypeError:
            # если твой _fetch_logs_resilient не принимает address — фильтруем вручную ниже
            return _fetch_logs_resilient(ev, frm, to)

    while cur <= to_block:
        end = min(cur + BATCH_SIZE - 1, to_block)
        try:
            created_logs  = _fetch(EV_CREATED, cur, end)
            released_logs = _fetch(EV_RELEASE, cur, end)
            refunded_logs = _fetch(EV_REFUND,  cur, end)

            # --- Created (депозит) ---
            for ev in created_logs:
                try:
                    # фильтруем чужие контракты, если address не был применён в запросе
                    if not escrow_addr or Web3.to_checksum_address(ev["address"]) != escrow_addr:
                        applied["skipped"] += 1
                        continue

                    args     = ev["args"]
                    order_id = args.get("orderId") or args.get("id") or args.get("orderID")
                    token    = args.get("token")
                    amount   = args.get("amount")
                    txh      = ev["transactionHash"].hex()
                    buyer_onchain  = args.get("buyer")
                    seller_onchain = args.get("seller")
                    buyer_onchain  = Web3.to_checksum_address(buyer_onchain) if buyer_onchain else None
                    seller_onchain = Web3.to_checksum_address(seller_onchain) if seller_onchain else None

                    if not order_id or token is None or amount is None:
                        applied["skipped"] += 1; continue

                    token = Web3.to_checksum_address(token)
                    if token != USDT_ADDR:
                        # игнорируем депозиты не в нашем токене
                        applied["skipped"] += 1; continue

                    amount = int(amount)

                    with transaction.atomic():
                        esc = (Escrow.objects
                               .select_for_update()
                               .filter(external_order_id=str(order_id))
                               .first())
                        if not esc:
                            applied["skipped"] += 1; continue

                        # идемпотентность по депозиту
                        if esc.deposit_tx and esc.deposit_tx.lower() == txh.lower():
                            continue

                        # сверка суммы
                        expected = _amount_expected_for_escrow(esc)
                        if expected != amount:
                            logger.warning(f"[sync] amount mismatch oid={order_id}: onchain={amount} expected={expected}")
                            applied["skipped"] += 1; continue

                        dirty = []
                        if esc.status != Escrow.Status.HELD:
                            esc.status = Escrow.Status.HELD
                            dirty.append("status")

                        esc.deposit_tx = txh
                        dirty.append("deposit_tx")

                        # заполняем buyer/seller адреса только если они ещё пустые
                        if buyer_onchain and not getattr(esc, "buyer_address", None):
                            esc.buyer_address = buyer_onchain
                            dirty.append("buyer_address")

                        if seller_onchain and not getattr(esc, "seller_address", None):
                            esc.seller_address = seller_onchain
                            dirty.append("seller_address")

                        esc.save(update_fields=dirty)

                        # отметим заказы (если модели хранят onchain_status/tx поля)
                        ArtworkOrder.objects.filter(external_order_id=order_id).update(
                            onchain_status="DEPOSITED", deposit_tx=txh, status=ArtworkOrder.Status.PENDING
                        )
                        TextProductOrder.objects.filter(external_order_id=order_id).update(
                            onchain_status="DEPOSITED", deposit_tx=txh
                        )

                        # декремент копий для арта — только при первом создании Escrow
                        # (у тебя раньше было при created=True; здесь escrow уже существует.
                        # Если нужна логика декремента при первом DEPOSIT — проверь признак:
                        if esc.order_ct_id == art_ct.id and not esc.release_tx and not esc.refund_tx:
                            art_orders = ArtworkOrder.objects.filter(external_order_id=order_id)
                            for ao in art_orders:
                                art = Artwork.objects.select_for_update().get(pk=ao.artwork_id)
                                if art.available_copies > 0:
                                    art.available_copies -= 1
                                    if art.available_copies <= 0:
                                        art.is_active = False
                                    art.save(update_fields=["available_copies", "is_active"])

                        applied["created"] += 1

                except Exception as exi:
                    logger.error(f"[sync] created err: {exi}", exc_info=True)
                    applied["skipped"] += 1

            # --- Released ---
            for ev in released_logs:
                try:
                    if Web3.to_checksum_address(ev["address"]) != ESCROW_ADDR:
                        applied["skipped"] += 1; continue

                    args     = ev["args"]
                    order_id = args.get("orderId") or args.get("id") or args.get("orderID")
                    txh      = ev["transactionHash"].hex()
                    if not order_id:
                        applied["skipped"] += 1; continue

                    with transaction.atomic():
                        esc = (Escrow.objects
                               .select_for_update()
                               .filter(external_order_id=str(order_id))
                               .first())
                        if not esc:
                            applied["skipped"] += 1; continue

                        # идемпотентность
                        if esc.release_tx and esc.release_tx.lower() == txh.lower():
                            continue

                        esc.status = Escrow.Status.RELEASED
                        esc.release_tx = txh
                        esc.released_at = timezone.now()
                        # снимаем флаги спора
                        dirty = ["status", "release_tx", "released_at"]
                        if esc.disputed or esc.moderator_locked:
                            esc.disputed = False
                            esc.moderator_locked = False
                            dirty += ["disputed", "moderator_locked"]
                        esc.save(update_fields=dirty)

                        # заказы
                        ArtworkOrder.objects.filter(external_order_id=order_id).update(
                            onchain_status="RELEASED", release_tx=txh, status=ArtworkOrder.Status.RELEASED
                        )
                        TextProductOrder.objects.filter(external_order_id=order_id).update(
                            onchain_status="RELEASED", release_tx=txh
                        )

                        applied["released"] += 1

                    try:
                        if esc.order_ct_id == art_ct.id:
                            for ao in ArtworkOrder.objects.filter(external_order_id=order_id):
                                art = Artwork.objects.get(pk=ao.artwork_id)
                                seller = art.owner
                                price = art.price
                                from ..views import _bump_popularity_for_sale
                                _bump_popularity_for_sale(seller, price)
                        elif esc.order_ct_id == text_ct.id:
                            for to in TextProductOrder.objects.filter(external_order_id=order_id):
                                product = to.product
                                seller = product.owner
                                price = product.price
                                from ..views import _bump_popularity_for_sale
                                _bump_popularity_for_sale(seller, price)
                    except Exception as ex:
                        logger.error(f"[popularity_boost_onchain] oid={order_id} err={ex}")

                except Exception as exi:
                    logger.error(f"[sync] release err: {exi}", exc_info=True)
                    applied["skipped"] += 1

            # --- Refunded ---
            for ev in refunded_logs:
                try:
                    if Web3.to_checksum_address(ev["address"]) != ESCROW_ADDR:
                        applied["skipped"] += 1; continue

                    args     = ev["args"]
                    order_id = args.get("orderId") or args.get("id") or args.get("orderID")
                    txh      = ev["transactionHash"].hex()
                    if not order_id:
                        applied["skipped"] += 1; continue

                    with transaction.atomic():
                        esc = (Escrow.objects
                               .select_for_update()
                               .filter(external_order_id=str(order_id))
                               .first())
                        if not esc:
                            applied["skipped"] += 1; continue

                        if esc.refund_tx and esc.refund_tx.lower() == txh.lower():
                            continue

                        esc.status = Escrow.Status.REFUNDED
                        esc.refund_tx = txh
                        esc.refunded_at = timezone.now()
                        dirty = ["status", "refund_tx", "refunded_at"]
                        if esc.disputed or esc.moderator_locked:
                            esc.disputed = False
                            esc.moderator_locked = False
                            dirty += ["disputed", "moderator_locked"]
                        esc.save(update_fields=dirty)

                        ArtworkOrder.objects.filter(external_order_id=order_id).update(
                            onchain_status="REFUNDED", refund_tx=txh, status=ArtworkOrder.Status.REFUNDED
                        )
                        TextProductOrder.objects.filter(external_order_id=order_id).update(
                            onchain_status="REFUNDED", refund_tx=txh
                        )

                        # (опционально) вернуть копию в тираж:
                        # for ao in ArtworkOrder.objects.filter(external_order_id=order_id):
                        #     art = Artwork.objects.select_for_update().get(pk=ao.artwork_id)
                        #     art.available_copies += 1
                        #     if art.available_copies > 0:
                        #         art.is_active = True
                        #     art.save(update_fields=["available_copies", "is_active"])

                        applied["refunded"] += 1

                except Exception as exi:
                    logger.error(f"[sync] refund err: {exi}", exc_info=True)
                    applied["skipped"] += 1

            # сдвигаем курсор
            with transaction.atomic():
                cursor.last_block = end
                cursor.save(update_fields=["last_block"])

        except Exception as exc:
            logger.error(f"[sync_escrow_events] range={cur}-{end} err={exc}", exc_info=True)
            raise self.retry(exc=exc)

        cur = end + 1

    return {"from": start, "to": to_block, **applied}





@shared_task(bind=True, max_retries=0)
def mint_osp_for_user(self, user_id: int, delta_micros: int):
    """
    delta_micros: целое (может быть отрицательным). Единица — микродоллар (1e-6 USD).
    На переходном этапе поддерживаем синхронизацию со старым Decimal-полем balance.
    """
    try:
        with transaction.atomic():
            wallet = (CryptoWallet.objects
                      .select_for_update()
                      .get(user_id=user_id, currency=Currency.OSP))

            # 1) новое целочисленное поле
            new_micros = wallet.balance_micros + int(delta_micros)
            if new_micros < 0:
                raise ValueError("Insufficient funds (micros)")
            wallet.balance_micros = new_micros

            # 2) legacy Decimal (держим в синхроне до финальной чистки)
            delta_dec = Decimal(int(delta_micros)) / MICROS
            new_dec = (wallet.balance or Decimal('0')) + delta_dec
            if new_dec < 0:
                raise ValueError("Insufficient funds (decimal)")
            wallet.balance = new_dec

            wallet.save(update_fields=['balance_micros', 'balance'])

        logger.info(f"[mint_osp_for_user] user={user_id} Δ={delta_micros}µ -> {new_micros}µ")
        return {"user_id": user_id, "delta_micros": int(delta_micros), "new_micros": new_micros}

    except CryptoWallet.DoesNotExist:
        logger.error(f"[mint_osp_for_user] OSP wallet not found for user_id={user_id}")
        return {"error": "wallet_not_found", "user_id": user_id}
    except Exception as e:
        logger.exception(f"[mint_osp_for_user] unexpected error: {e}")
        raise

@shared_task(bind=True, max_retries=0, acks_late=True)
def auto_release_held_escrows(self):
    """
    Батч-обработка истёкших escrow:
      - выбираем HELD с auto_release_at <= now c блокировкой;
      - OSP (оффчейн): сразу помечаем RELEASED и начисляем seller'у после коммита;
      - USDT/USDC (ончейн): после коммита отправляем release(orderId) в контракт,
        локально сохраняем release_tx, а финальный статус подтянет sync_escrow_events.
    """
    released_offchain = 0
    released_onchain_enqueued = 0

    ct_text = ContentType.objects.get_for_model(TextProductOrder)
    ct_art  = ContentType.objects.get_for_model(ArtworkOrder)

    # Будем повторять, пока есть партии под замок
    while True:
        now = timezone.now()

        with transaction.atomic():
            escrows = list(
                Escrow.objects
                .select_for_update(skip_locked=True)
                .filter(
                    status=Escrow.Status.HELD, 
                    auto_release_at__lte=now,
                    disputed=False,        
                    moderator_locked=False,                    
                    )
                .order_by('auto_release_at')[:BATCH_LIMIT]
            )
            if not escrows:
                break

            post_commit_settlements: list[tuple[int, int, int, str]] = []  # (seller_id, amount_micros, escrow_id, order_ref)
            onchain_release_jobs: list[tuple[int, str]] = []
            
            for e in escrows:
                try:
                    order = e.order_obj  # один доступ к GFK под замком
                except Exception:
                    continue

                # Определяем валюту заказа
                curr = None
                if e.order_ct_id == ct_text.id:
                    # TextProductOrder
                    try:
                        curr = getattr(order.product, "currency", None)
                    except Exception:
                        curr = None
                elif e.order_ct_id == ct_art.id:
                    # ArtworkOrder
                    try:
                        curr = getattr(order.artwork, "currency", None)
                    except Exception:
                        curr = None

                if curr == Currency.OSP:
                    # 0) защитимся от повторной обработки
                    if e.status != Escrow.Status.HELD:
                        continue

                    # 1) пометить escrow RELEASED
                    e.status = Escrow.Status.RELEASED
                    e.released_at = now
                    e.save(update_fields=['status', 'released_at'])
                    released_offchain += 1

                    # 2) закрыть заказ (если у модели есть такие статусы — учитываем мягко)
                    try:
                        if hasattr(order, "Status"):
                            if hasattr(order.Status, "RELEASED"):
                                order.status = order.Status.RELEASED
                            elif hasattr(order.Status, "COMPLETED"):
                                order.status = order.Status.COMPLETED
                            order.save(update_fields=["status"])
                    except Exception:
                        pass

                    try:
                        price = getattr(order, "price", None)
                        seller = getattr(order, "seller", None)
                        if seller and price:
                            from ..views import _bump_popularity_for_sale
                            _bump_popularity_for_sale(seller, price)
                    except Exception as ex:
                        logger.error(f"[popularity_boost] seller={getattr(order, 'seller_id', None)} err={ex}")



                    # 3) гарантировать запись Purchase для арта (на случай, если не создана ранее)
                    try:
                        if isinstance(order, ArtworkOrder):
                            Purchase.objects.get_or_create(user=e.buyer, artwork=order.artwork)
                    except Exception:
                        # не валим батч — просто логируй, если нужно
                        pass

                    # 4) подготовить пост-коммит действия:
                    amount_i = int(e.amount_micros)
                    order_ref = getattr(order, "external_order_id", None) or str(e.id)
                    post_commit_settlements.append((e.seller_id, amount_i, e.id, order_ref))

            # После коммита: запустить начисления OSP и ончейн-релизы
            def _enqueue_after_commit():
                # 1) оффчейн OSP
                from ..money import settle_osp_release_with_fee
                for seller_id, amount, escrow_id, order_ref in post_commit_settlements:
                    settle_osp_release_with_fee.delay(seller_id, amount, escrow_id, order_ref)
                # 2) ончейн релиз — одна транза на каждый escrow
                sender_addr = getattr(settings, "ESCROW_EXECUTOR_ADDRESS", None)
                if not sender_addr:
                    return
                for escrow_id, oid in onchain_release_jobs:
                    try:
                        tx = build_release_tx(oid, sender_addr)
                        tx_hash = send_tx(tx)
                        Escrow.objects.filter(pk=escrow_id).update(release_tx=tx_hash)
                    except Exception as ex:
                        logger.error(f"[auto_release_onchain] escrow_id={escrow_id} oid={oid} err={ex}")
            
            if post_commit_settlements or onchain_release_jobs:
                released_onchain_enqueued += len(onchain_release_jobs)
                transaction.on_commit(_enqueue_after_commit)

    return {
        "released_offchain": released_offchain,
        "onchain_release_enqueued": released_onchain_enqueued,
    }