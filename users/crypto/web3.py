from web3 import Web3
from django.conf import settings

# — безопасный конструктор провайдера —
def get_w3():
    rpc = getattr(settings, "ETH_RPC_URL", None)
    return Web3(Web3.HTTPProvider(rpc)) if rpc else None

w3 = get_w3()
web3 = w3  # alias

# — ABI и checksum-адрес эскроу — безопасно (может быть None) —
def get_escrow_address_checksum():
    addr = (getattr(settings, "ESCROW_CONTRACT_ADDRESS", "") or "").strip()
    if not addr or addr == "0x" or len(addr) < 42:
        return None
    try:
        return Web3.to_checksum_address(addr)
    except Exception:
        return None

escrow_abi = getattr(settings, "ESCROW_ABI", []) or []
escrow_address = get_escrow_address_checksum()
escrow = None
if w3 and escrow_address and escrow_abi:
    escrow = w3.eth.contract(address=escrow_address, abi=escrow_abi)

# на совместимость со старым именем
escrow_contract = escrow

# ===== ERC20 helpers (безопасные) =====
ERC20_ABI_MIN = [
    {"constant": False, "inputs": [{"name":"_to","type":"address"},{"name":"_value","type":"uint256"}],
     "name": "transfer", "outputs":[{"name":"","type":"bool"}], "payable": False,
     "stateMutability":"nonpayable","type":"function"},
    {"constant": True, "inputs": [{"name":"_owner","type":"address"}],
     "name":"balanceOf", "outputs":[{"name":"balance","type":"uint256"}],
     "payable": False, "stateMutability":"view","type":"function"},
    {"constant": True, "inputs": [], "name":"decimals",
     "outputs":[{"name":"","type":"uint8"}], "payable": False,
     "stateMutability":"view","type":"function"},
]

def erc20_contract(token_addr: str):
    if not (w3 and token_addr):
        return None
    return w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=ERC20_ABI_MIN)

def erc20_decimals(token_addr: str) -> int:
    try:
        c = erc20_contract(token_addr)
        return c.functions.decimals().call() if c else int(getattr(settings, "USDT_DECIMALS", 18))
    except Exception:
        return int(getattr(settings, "USDT_DECIMALS", 18))

def erc20_balance(token_addr: str, owner: str) -> int:
    c = erc20_contract(token_addr)
    return int(c.functions.balanceOf(Web3.to_checksum_address(owner)).call()) if c else 0

# ===== Ончейн билдеры (ленивые с проверками) =====
def _ensure_ready():
    if not w3:
        raise RuntimeError("ETH_RPC_URL is not configured")
    if not escrow:
        raise RuntimeError("ESCROW_CONTRACT_ADDRESS/ESCROW_ABI are not configured")

def build_release_tx(order_id_hex: str, from_addr: str):
    _ensure_ready()
    from_addr = Web3.to_checksum_address(from_addr)
    return escrow.functions.release(order_id_hex).build_transaction({
        "from": from_addr,
        "nonce": w3.eth.get_transaction_count(from_addr),
    })

def build_refund_tx(order_id_hex: str, from_addr: str):
    _ensure_ready()
    from_addr = Web3.to_checksum_address(from_addr)
    return escrow.functions.refund(order_id_hex).build_transaction({
        "from": from_addr,
        "nonce": w3.eth.get_transaction_count(from_addr),
    })

def build_erc20_transfer_tx(token_addr: str, to_addr: str, amount_base_units: int, from_addr: str):
    if not w3:
        raise RuntimeError("ETH_RPC_URL is not configured")
    token = w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=ERC20_ABI_MIN)
    from_addr = Web3.to_checksum_address(from_addr)
    return token.functions.transfer(
        Web3.to_checksum_address(to_addr),
        int(amount_base_units)
    ).build_transaction({
        "from": from_addr,
        "nonce": w3.eth.get_transaction_count(from_addr),
    })

# чтобы импорт account не падал, если он ещё не инициализирован
account = None
