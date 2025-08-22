# users/admin.py

from django.contrib import admin
from .models import TextProduct, Artwork, CustomUser

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
    list_display = ('username','email','is_active','is_staff')
    search_fields = ('username','email')
    actions = ['block_users','unblock_users']

    def block_users(self, request, queryset):
        queryset.update(is_active=False)
    def unblock_users(self, request, queryset):
        queryset.update(is_active=True)
