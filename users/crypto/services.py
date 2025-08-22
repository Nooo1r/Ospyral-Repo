import json
from pathlib import Path
from decimal import Decimal
from django.conf import settings
from .web3 import web3 

# Пусть у вас в проекте лежит JSON-файл с ABI ERC-20 (например, data/erc20_abi.json)
ABI_PATH = Path(settings.BASE_DIR) / "data" / "erc20_abi.json"
with open(ABI_PATH, "r", encoding="utf-8") as f:
    ERC20_ABI = json.load(f)

def get_eth_balance(address: str) -> Decimal:
    """
    Возвращает баланс ETH на адресе в единицах ETH (не wei).
    """
    balance_wei = web3.eth.get_balance(address)
    return Decimal(web3.fromWei(balance_wei, "ether"))

def get_erc20_balance(token_address: str, wallet_address: str) -> Decimal:
    """
    Возвращает баланс ERC-20 токена по адресу контрактa и кошелька.
    Результат — в токенах (с учётом 18 десятичных по стандарту).
    """
    contract = web3.eth.contract(address=token_address, abi=ERC20_ABI)
    balance = contract.functions.balanceOf(wallet_address).call()
    # Обычно у токенов 18 десятичных, можно вынести в settings, если нужно
    return Decimal(balance) / Decimal(10 ** 18)
