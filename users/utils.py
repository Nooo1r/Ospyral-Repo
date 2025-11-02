from django.conf import settings
from cryptography.fernet import Fernet, InvalidToken
import os, time, hashlib
from typing import Iterable, Union
from django.contrib.sessions.models import Session
import json
from django.utils import timezone
from django.db import transaction


def _iter_keys() -> Iterable[bytes]:
    """
    Возвращает ключи: сначала главный, затем легаси (если заданы),
    с бэкапом на DEFAULT_ENCRYPTION_KEY для совместимости.
    """
    keys = []

    # 1) Современный список ключей (главный + легаси)
    if hasattr(settings, "ENCRYPTION_KEYS"):
        for k in settings.ENCRYPTION_KEYS:
            if isinstance(k, str):
                keys.append(k.encode("utf-8"))
            elif isinstance(k, (bytes, bytearray)):
                keys.append(bytes(k))

    # 2) Совместимость: если список отсутствует, используем ENCRYPTION_KEY
    elif hasattr(settings, "ENCRYPTION_KEY"):
        k = settings.ENCRYPTION_KEY
        keys.append(k.encode("utf-8") if isinstance(k, str) else bytes(k))

    # 3) Совместимость со старым именем
    if hasattr(settings, "DEFAULT_ENCRYPTION_KEY"):
        dk = settings.DEFAULT_ENCRYPTION_KEY
        dk_bytes = dk.encode("utf-8") if isinstance(dk, str) else bytes(dk)
        # не дублируем, если уже есть
        if dk_bytes not in keys:
            keys.append(dk_bytes)

    return keys


def encrypt_text(plaintext: Union[str, bytes]) -> bytes:
    """
    Шифруем ТОЛЬКО главным ключом (settings.ENCRYPTION_KEY).
    Возвращаем байтовый токен Fernet.
    """
    if plaintext is None:
        return b""
    if isinstance(plaintext, str):
        plaintext = plaintext.encode("utf-8")

    primary = getattr(settings, "ENCRYPTION_KEY", None)
    if primary is None:
        raise RuntimeError("ENCRYPTION_KEY is not configured in settings")

    primary_bytes = primary.encode("utf-8") if isinstance(primary, str) else bytes(primary)
    return Fernet(primary_bytes).encrypt(plaintext)


def decrypt_text(encrypted_content: Union[str, bytes], owner=None) -> str:
    """
    Пытаемся расшифровать encrypted_content любым известным ключом.
    При неудаче возвращаем исходное значение (как и раньше).
    Параметр owner сохраняем для совместимости сигнатуры.
    """
    if not encrypted_content:
        return ""

    token = encrypted_content
    if isinstance(token, str):
        token = token.encode("utf-8")

    last_err = None
    for key_bytes in _iter_keys():
        try:
            decrypted = Fernet(key_bytes).decrypt(token)
            return decrypted.decode("utf-8")
        except InvalidToken as e:
            last_err = e
            continue
        except Exception as e:
            last_err = e
            continue

    # Совместимо с прежней логикой: ничего не упадёт, вернём как есть
    return encrypted_content if isinstance(encrypted_content, str) else encrypted_content.decode("utf-8", "ignore")

def make_order_id_hex(user_id: int, product_kind: str, product_id: int) -> str:
    """
    Возвращает 0x + 64 hex (bytes32-подобный id).
    """
    seed = f"{user_id}:{product_kind}:{product_id}:{time.time_ns()}:{os.urandom(8).hex()}"
    h = hashlib.sha256(seed.encode("utf-8")).hexdigest()  # 64 hex
    return "0x" + h


def kill_user_sessions(user_id: int):
    for s in Session.objects.all():
        try:
            data = s.get_decoded()
        except Exception:
            # Fallback: иногда полезно распарсить вручную
            try:
                data = json.loads(s.session_data)
            except Exception:
                data = {}
        if str(data.get('_auth_user_id', '')) == str(user_id):
            s.delete()

from .crypto.web3 import web3, account

def send_tx(fn, *args, value: int = 0):
    """
    Универсальный отправщик транзакций:
      - fn       — метод contract.functions.* (не вызывайте fn, передайте его как fn)
      - args     — аргументы для функции
      - value    — сумма в Wei (по умолчанию 0)
    Возвращает кортеж (tx_hash_hex, receipt).
    """
    # порядковый номер транзакции
    nonce = web3.eth.get_transaction_count(account.address)
    # строим транзакцию
    tx = fn(*args).buildTransaction({
        'from': account.address,
        'nonce': nonce,
        'value': value,
        'gas': 200_000,
        'gasPrice': web3.eth.gas_price,
        'chainId': web3.eth.chain_id,
    })
    # подписываем и шлём
    signed = account.sign_transaction(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    # ждём включения в блок
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    return tx_hash.hex(), receipt

def grant_vip_gift(user, plan, *, hours:int|None, reason:str="", moderator=None):
    """
    Выдать VIP пользователю бесплатно:
      - если VIP уже активен и срок позже — продлеваем (берём max из текущего и now) + добавляем hours
      - если hours=None или 0 => бессрочно (НЕ рекомендуется, но допускаем)
    """
    profile = user.profile  # поправь, если профиль иначе доступен
    now = timezone.now()

    # вычисляем новый срок
    base_start = max(now, getattr(profile, "vip_expires_at", None) or now)
    ends_at = None
    if hours and hours > 0:
        ends_at = base_start + timezone.timedelta(hours=hours)

    with transaction.atomic():
        # записываем в профиль
        setattr(profile, "vip_plan", plan)
        setattr(profile, "vip_expires_at", ends_at)
        profile.save(update_fields=["vip_plan", "vip_expires_at"])

        # аудит
        from .models import VIPGiftRecord
        VIPGiftRecord.objects.create(
            user=user, plan=plan, reason=reason or f"Gift VIP {plan} на {hours or 0} ч.",
            starts_at=now, ends_at=ends_at, moderator=moderator
        )

def revoke_vip(user, *, reason:str="", moderator=None):
    """
    Снять активный VIP (досрочно).
    """
    profile = user.profile
    had_any = bool(getattr(profile, "vip_plan_id", None) or getattr(profile, "vip_expires_at", None))
    if not had_any:
        return

    setattr(profile, "vip_plan", None)
    setattr(profile, "vip_expires_at", None)
    profile.save(update_fields=["vip_plan", "vip_expires_at"])