import os
import json
from pathlib import Path
from decimal import Decimal

from web3 import Web3
from django.conf import settings

w3 = Web3(Web3.HTTPProvider(settings.ETH_RPC_URL))
web3 = w3  # совместимость, если где-то импортировали web3

# 2) Контракты (адреса/ABI берём ТОЛЬКО из settings)
escrow_contract = w3.eth.contract(
    address=Web3.to_checksum_address(settings.ESCROW_CONTRACT_ADDRESS),
    abi=settings.ESCROW_ABI,
)

# 3) USDT (тестовый) контракт
usdc_contract = None
if settings.USDC_TOKEN_ADDRESS and settings.USDC_ABI:
    usdc_contract = w3.eth.contract(
        address=Web3.to_checksum_address(settings.USDC_TOKEN_ADDRESS),
        abi=settings.USDC_ABI,
    )

account = None

# 4) Хелперы (без серверных подписей)
def get_chain_id() -> int:
    try:
        return w3.eth.chain_id
    except Exception:
        return int(getattr(settings, "CHAIN_ID", 0))

# кэшируем decimals по токенам, если нужно
from functools import lru_cache
@lru_cache(maxsize=16)
def erc20_decimals(token_address: str) -> int:
    if token_address == settings.ZERO_ADDRESS:
        return 18
    c = w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=settings.USDC_ABI)
    try:
        return c.functions.decimals().call()
    except Exception:
        return 18
