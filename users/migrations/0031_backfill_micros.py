from django.db import migrations
from decimal import Decimal, ROUND_HALF_UP

MICROS = Decimal('1000000')

def to_micros(d: Decimal | None) -> int:
    d = d or Decimal('0')
    return int((d * MICROS).quantize(Decimal('1'), rounding=ROUND_HALF_UP))

def fwd(apps, schema_editor):
    CryptoWallet = apps.get_model('users', 'CryptoWallet')
    CryptoTransaction = apps.get_model('users', 'CryptoTransaction')


    for w in CryptoWallet.objects.all().only('id', 'balance'):
        CryptoWallet.objects.filter(pk=w.pk).update(balance_micros=to_micros(w.balance))


    for tx in CryptoTransaction.objects.all().only('id', 'amount'):
        CryptoTransaction.objects.filter(pk=tx.pk).update(amount_micros=to_micros(tx.amount))

def bwd(apps, schema_editor):

    pass

class Migration(migrations.Migration):

    dependencies = [
        ('users', '0030_artworkorder_deposit_tx_and_more'),
    ]

    operations = [
        migrations.RunPython(fwd, bwd),
    ]
