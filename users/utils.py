from django.conf import settings
from cryptography.fernet import Fernet, InvalidToken

def decrypt_text(encrypted_content: str, owner):
    """
    Пытаемся расшифровать encrypted_content.
    Если это не валидный токен под нашим ключом, возвращаем то, что было в базе.
    """
    keys = [
        settings.ENCRYPTION_KEY,
        settings.DEFAULT_ENCRYPTION_KEY
    ]

    for raw_key in keys:
        try:
            f = Fernet(raw_key.encode('utf-8'))
            decrypted = f.decrypt(encrypted_content.encode('utf-8'))
            return decrypted.decode('utf-8')
        except InvalidToken:
            continue
        except Exception:
            continue

    # ни один ключ не подошёл
    return encrypted_content

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