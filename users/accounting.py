
from django.db import transaction
from decimal import Decimal
from .models import CryptoWallet, Currency, LedgerEntry

MICRO = Decimal("1000000")

def decimal_to_micros(amount: Decimal) -> int:
    return int((amount * MICRO).to_integral_value())

def micros_to_decimal(micros: int) -> Decimal:
    return (Decimal(micros) / MICRO).quantize(Decimal("0.000001"))


def book_osp(
    wallet: CryptoWallet,
    *,
    kind: str,
    reference: str,
    delta_micros: int,
    tx_hash: str = ""
) -> LedgerEntry:

    if wallet.currency != Currency.OSP:
        raise ValueError("Ожидался OSP-кошелёк")
    if not isinstance(delta_micros, int):
        raise ValueError("delta_micros должен быть int (микро-OSP)")
    if delta_micros == 0:
        return

    with transaction.atomic():

        locked = (CryptoWallet.objects
                  .select_for_update()
                  .get(pk=wallet.pk))

        before = int(locked.balance_micros)
        after  = before + int(delta_micros)
        if after < 0:
            raise ValueError("Недостаточно средств")

        locked.balance_micros = after

        locked.balance = (Decimal(after) / MICRO)
        locked.save(update_fields=["balance_micros", "balance"])

        entry = LedgerEntry.objects.create(
            user=locked.user,
            wallet=locked,
            side=LedgerEntry.Side.CREDIT if delta_micros > 0 else LedgerEntry.Side.DEBIT,
            kind=kind,
            currency=Currency.OSP,
            amount_micros=abs(delta_micros),
            balance_after_micros=after,
            reference=reference,
            external_tx_hash=tx_hash,
        )

        return entry
