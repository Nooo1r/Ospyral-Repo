
from django.contrib import admin
from .models import (
    TextProduct, Artwork, 
    CustomUser, LedgerEntry,
    VIPPlan,
    BannedFingerprint, News,
    )
from .money import micros_to_usd


@admin.register(News)
class NewsAdmin(admin.ModelAdmin):
    list_display  = ('title', 'published_at', 'is_published', 'author')
    list_filter   = ('is_published', 'published_at')
    search_fields = ('title', 'content')
    date_hierarchy = 'published_at'
    ordering = ('-published_at',)

@admin.register(TextProduct)
class TextProductAdmin(admin.ModelAdmin):
    list_display = ('title', 'owner', 'status', 'created_at')
    list_filter  = ('status', 'owner')
    search_fields = ('title', 'description', 'owner__username')

    # Уберём 'updated_at', если его нет в модели. 
    # Оставим только реально существующие поля:
    readonly_fields = (
        'encrypted_content',    # если поле действительно называется именно так
        'rejection_reason',     # поле "причина отклонения"
        'created_at',
        # 'updated_at',  ← убрано, потому что такого поля в модели нет
    )

    fieldsets = (
        (None, {
            'fields': (
                'title',
                'owner',
                'category',
                'status',
                'price',
                'currency',
            )
        }),
        ('Технические поля', {
            'classes': ('collapse',),
            'fields': (
                'encrypted_content',
                'rejection_reason',
                'created_at',
                # 'updated_at',  ← убрано
            ),
        }),
    )

    # Если у вас действительно есть функция расшифровки:
    def encrypted_content(self, obj):
        from .utils import decrypt_text  # ← убедитесь, что файл users/utils.py существует 
        try:
            return decrypt_text(obj.encrypted_content, obj.owner)
        except Exception:
            return '— не удалось расшифровать —'
    encrypted_content.short_description = "Полное содержание (дешифрованный текст)"


@admin.register(Artwork)
class ArtworkAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'title', 'owner', 'currency', 'price',
        'status', 'is_active', 'available_copies', 'created'
    )
    list_filter = ('status', 'currency', 'is_active', 'nsfw')
    search_fields = ('title', 'description', 'owner__username', 'owner__email')
    readonly_fields = ('approved_at', 'rejection_reason', 'created', 'updated', 'preview_image', 'thumbnail')

    actions = ['block_artworks', 'unblock_artworks']

    fieldsets = (
        (None, {
            'fields': (
                'owner', 'title', 'description', 'keywords', 'category',
                'currency', 'price', 'available_copies',
                'status', 'is_active', 'nsfw'
            )
        }),
        ('Изображения', {
            'fields': ('original_image', 'preview_image', 'thumbnail')
        }),
        ('Модерация / блокировки', {
            'classes': ('collapse',),
            'fields': (
                'blocked_until', 'block_reason',
                'approved_at', 'rejection_reason',
                'censored_areas',
            )
        }),
    )

    def block_artworks(self, request, queryset):
        queryset.update(is_active=False)
    block_artworks.short_description = 'Снять с продажи выбранные работы'

    def unblock_artworks(self, request, queryset):
        queryset.update(is_active=True)
    unblock_artworks.short_description = 'Вернуть в продажу выбранные работы'


@admin.register(CustomUser)
class CustomUserAdmin(admin.ModelAdmin):
    list_display = ('username','email','is_active','is_staff','is_banned','ban_until')
    search_fields = ('username','email')
    actions = ['ban_7d', 'ban_30d', 'ban_forever', 'unban_users']

    def ban_7d(self, request, qs):
        from django.utils import timezone
        from .utils import kill_user_sessions
        until = timezone.now() + timezone.timedelta(days=7)
        reason = "Нарушение правил (7 дней)"
        for u in qs:
            u.is_banned = True
            u.ban_reason = reason
            u.ban_until = until
            u.save(update_fields=['is_banned','ban_reason','ban_until'])
            kill_user_sessions(u.id)
    ban_7d.short_description = "Забанить на 7 дней"

    def ban_30d(self, request, qs):
        from django.utils import timezone
        from .utils import kill_user_sessions
        until = timezone.now() + timezone.timedelta(days=30)
        reason = "Нарушение правил (30 дней)"
        for u in qs:
            u.is_banned = True
            u.ban_reason = reason
            u.ban_until = until
            u.save(update_fields=['is_banned','ban_reason','ban_until'])
            kill_user_sessions(u.id)
    ban_30d.short_description = "Забанить на 30 дней"

    def ban_forever(self, request, qs):
        from .utils import kill_user_sessions
        for u in qs:
            u.is_banned = True
            u.ban_reason = "Нарушение правил (навсегда)"
            u.ban_until = None
            u.save(update_fields=['is_banned','ban_reason','ban_until'])
            kill_user_sessions(u.id)
    ban_forever.short_description = "Забанить навсегда"

    def unban_users(self, request, qs):
        for u in qs:
            u.is_banned = False
            u.ban_reason = ""
            u.ban_until = None
            u.save(update_fields=['is_banned','ban_reason','ban_until'])
    unban_users.short_description = "Снять бан"

@admin.register(BannedFingerprint)
class BannedFingerprintAdmin(admin.ModelAdmin):
    list_display = ("ip_hash", "ua_hash", "device_id", "expires_at", "created_at")
    list_filter  = ("expires_at", "created_at")
    search_fields = ("ip_hash", "ua_hash", "device_id")
    readonly_fields = ("created_at",)

@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = ("created_at","user","wallet","side","kind","currency","amount_micros","balance_after_micros","reference","external_tx_hash")
    list_filter  = ("side","kind","currency","created_at")
    search_fields= ("reference","external_tx_hash","user__email","user__username")
    date_hierarchy = "created_at"
    ordering = ("-created_at",) 
    list_select_related = ("user","wallet")
    raw_id_fields = ("user","wallet")
    
def format_osp(micros: int) -> str:
    d = micros_to_usd(micros)  # Decimal с 6 знаками после запятой
    s = format(d.normalize(), 'f')  # без научной нотации
    if '.' in s:
        s = s.rstrip('0').rstrip('.')
    return f"{s} OSP"


@admin.register(VIPPlan)
class VIPPlanAdmin(admin.ModelAdmin):
    list_display = (
        "code", "title", "level", "duration_days",
        "price_pretty", "popularity_boost", "daily_quota",
        "min_interval_days", "is_active",
    )
    list_filter  = ("level", "is_active")
    search_fields = ("code", "title")
    ordering = ("level", "duration_days")

    # показываем цену в OSP, используя money.py
    def price_pretty(self, obj):
        return format_osp(obj.price_osp_micros)
    price_pretty.short_description = "Цена (OSP)"