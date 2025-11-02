from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from ...models import TextProduct  
from ...utils import decrypt_text  

class Command(BaseCommand):
    help = (
        "Re-encrypt TextProduct.encrypted_content to the PRIMARY ENCRYPTION_KEY.\n"
        "By default runs in dry-run mode (no changes). Use --confirm to write."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Actually write changes (without this flag it is a dry-run).",
        )
        parser.add_argument(
            "--ids",
            nargs="+",
            type=int,
            help="Optional list of product IDs to process (otherwise processes all).",
        )
        parser.add_argument(
            "--batch",
            type=int,
            default=500,
            help="Iterator chunk size (default: 500).",
        )

    def handle(self, *args, **opts):
        confirm = opts["confirm"]
        ids     = opts.get("ids") or []
        batch   = int(opts.get("batch") or 500)

        qs = TextProduct.objects.all().only("id", "encrypted_content")
        if ids:
            qs = qs.filter(id__in=ids)

        total = 0
        empty = 0
        ok_same = 0         
        changed = 0         
        failed = 0          

        self.stdout.write(self.style.NOTICE(
            f"Starting re-encryption (mode={'CONFIRM' if confirm else 'DRY-RUN'})"
        ))
        start_ts = timezone.now()

        iterator = qs.iterator(chunk_size=batch)


        ctx = transaction.atomic() if confirm else _NullContext()
        with ctx:
            for tp in iterator:
                total += 1
                token = (tp.encrypted_content or "").strip()

                if not token:
                    empty += 1
                    continue

                try:

                    plain = decrypt_text(token)
                except Exception:

                    failed += 1
                    self.stdout.write(self.style.ERROR(f"[{tp.id}] cannot decrypt"))
                    continue


                if plain == token:
                    failed += 1
                    self.stdout.write(self.style.ERROR(f"[{tp.id}] cannot decrypt (got same token back)"))
                    continue


                new_token = tp.encrypt_content(plain)  


                if new_token == token:
                    ok_same += 1
                    continue


                changed += 1
                if confirm:
                    tp.encrypted_content = new_token
                    tp.save(update_fields=["encrypted_content"])

        took = (timezone.now() - start_ts).total_seconds()
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=== Re-encryption summary ==="))
        self.stdout.write(f"Mode         : {'CONFIRM (write)' if confirm else 'DRY-RUN (no changes)'}")
        self.stdout.write(f"Processed    : {total}")
        self.stdout.write(f"Empty tokens : {empty}")
        self.stdout.write(f"Already OK   : {ok_same}")
        self.stdout.write(f"Need change  : {changed}")
        self.stdout.write(f"Failed       : {failed}")
        self.stdout.write(f"Took         : {took:.2f}s")

class _NullContext:
    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb): return False
