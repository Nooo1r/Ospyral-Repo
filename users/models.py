from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.core.files.base import ContentFile
from django.conf import settings
from django.templatetags.static import static
from cryptography.fernet import Fernet
from django.utils import timezone
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from PIL import Image, ImageFilter, ImageDraw
import io
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db.models import Q, CheckConstraint

import uuid
from django.db.models.signals import post_save

from django.dispatch import receiver
from django.core.validators import RegexValidator
from datetime import timedelta

HEX_66 = RegexValidator(r'^0x[a-fA-F0-9]{64}$', 'Ожидается 0x + 64 hex-символа.')


class EmailVerification(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='email_verifications'
    )
    code       = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    def is_expired(self):
        return timezone.now() > self.expires_at

    def __str__(self):
        return f"{self.user.email} → {self.code}"


class CustomUserManager(BaseUserManager):
    def create_user(self, email, username, password=None, **extra_fields):
        if not email:
            raise ValueError('Email обязателен')
        if not username:
            raise ValueError('Username обязателен')
        email = self.normalize_email(email)
        user = self.model(email=email, username=username, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, username, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return self.create_user(email, username, password, **extra_fields)

class CustomUser(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    username = models.CharField(max_length=150, unique=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    profile_image = models.ImageField(upload_to='profile_images/', null=True, blank=True)
    is_email_verified = models.BooleanField(
        default=False,
        help_text="True после успешного ввода кода из письма"
    )
    objects = CustomUserManager()


    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    @property
    def profile_image_url(self):
        if self.profile_image:
            return self.profile_image.url
        return static('images/spyral.svg')
    
    def full_name(self) -> str:
        try:
            prof = getattr(self, "profile", None)
            if prof and getattr(prof, "display_name", ""):
                return prof.display_name.strip()
        except Exception:
            pass
        return self.username

    def get_full_name(self) -> str:
        return self.full_name()

    def get_display_name(self) -> str:
        profile_name = ""
        try:
            profile_name = getattr(self, "profile", None) and (self.profile.display_name or "")
        except Exception:
            profile_name = ""
        return self.full_name()

    def get_short_name(self) -> str:
        return self.full_name()
    def __str__(self):
        return self.email
    
    is_banned   = models.BooleanField(
        default=False,
        help_text="Отметка для полной блокировки пользователя"
    )
    ban_reason  = models.CharField(
        max_length=200,
        blank=True,
        help_text="Причина блокировки"
    )
    ban_until   = models.DateTimeField(
        null=True, blank=True,
        help_text="Дата окончания бана (пусто — навсегда)"
    )

    def is_active_user(self):
        if self.is_banned:
            # автоматический анбан по истечению срока
            if self.ban_until and timezone.now() >= self.ban_until:
                self.is_banned = False
                self.ban_reason = ''
                self.ban_until = None
                self.save(update_fields=['is_banned','ban_reason','ban_until'])
            else:
                return False
        return self.is_active


class BannedFingerprint(models.Model):
    """
    Храним хеш IP, хеш User-Agent и/или произвольный device_id.
    Если любой из параметров совпадает – режем доступ.
    """
    ip_hash = models.CharField(max_length=64, blank=True, db_index=True)        # sha256(ip)
    ua_hash = models.CharField(max_length=64, blank=True, db_index=True)        # sha256(ua)
    device_id = models.CharField(max_length=128, blank=True, db_index=True)     # например, cookie/LocalStorage
    reason = models.CharField(max_length=200, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)                    # None = навсегда
    created_at = models.DateTimeField(auto_now_add=True)

    def active(self):
        return not self.expires_at or self.expires_at > timezone.now()

class BanRecord(models.Model):
    """
    Аудит банов/разбанов.
    """
    user       = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ban_records")
    action     = models.CharField(max_length=16, choices=(("BAN","BAN"),("UNBAN","UNBAN")))
    reason     = models.CharField(max_length=255, blank=True)
    until      = models.DateTimeField(null=True, blank=True)
    moderator  = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="moderated_bans")
    created_at = models.DateTimeField(auto_now_add=True)

HEX_COLOR_VALIDATOR = RegexValidator(
    regex=r'^#[0-9A-Fa-f]{6}$',
    message='Цвет должен быть в формате #RRGGBB'
)

class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    bio = models.CharField(max_length=150, blank=True, null=True, verbose_name="Описание профиля")
    background_color = models.CharField(max_length=20, default='#3498db')
    
    popularity = models.IntegerField(default=0)

    bg_color = models.CharField( 
        max_length=7,
        validators=[HEX_COLOR_VALIDATOR],
        blank=True,
        null=True,
        help_text='Цвет фона профиля/ленты (#RRGGBB)'
    )

    def __str__(self):
        return self.user.username


class Currency(models.TextChoices):
    USDT = 'USDT', 'Tether USD'
    OSP = 'OSP', 'Ospyral Coin'

def generate_wallet_address():
    # простая генерация уникального адреса; в проде может быть что-то сложнее
    return uuid.uuid4().hex

@receiver(post_save, sender=CustomUser)
def create_user_profile_and_wallet(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance)
        CryptoWallet.objects.create(
            user=instance,
            currency=Currency.OSP,
            address=generate_wallet_address()
        )

class CryptoWallet(models.Model):
    user      = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='crypto_wallets')
    currency  = models.CharField(max_length=4, choices=Currency.choices)
    address   = models.CharField(max_length=128, unique=True)   # ваш адрес (пример: ERC-20 или TRC-20)
    balance   = models.DecimalField(max_digits=30, decimal_places=18, default=0)  # высокая точность для токенов
    balance_micros = models.BigIntegerField(default=0)
    last_scanned_block = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    chain_id = models.IntegerField(default=getattr(settings, "CHAIN_ID", 56))  
    is_external = models.BooleanField(default=False)   
    is_primary  = models.BooleanField(default=False)     
    verified_at = models.DateTimeField(null=True, blank=True)
    verify_nonce = models.CharField(max_length=40, blank=True, default="")

    class Meta:
        unique_together = ('user', 'currency')

class CryptoTransaction(models.Model):
    wallet      = models.ForeignKey(CryptoWallet, on_delete=models.CASCADE, related_name='txs')
    tx_type     = models.CharField(max_length=20, choices=[('deposit','Deposit'),('escrow','Escrow'),
                                                            ('release','Release'),('purchase','Purchase')])
    amount      = models.DecimalField(max_digits=30, decimal_places=18)
    amount_micros = models.BigIntegerField(default=0)
    tx_hash     = models.CharField(max_length=66, blank=True)  # hash on-chain
    reference   = models.CharField(max_length=100, blank=True) # внешние ID
    created_at  = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["wallet", "tx_type", "reference"],
                name="uniq_crypto_tx_wallet_type_ref"
            ),
        ]
        indexes = [
            models.Index(fields=["reference"], name="idx_crypto_tx_reference"),
        ]



class UserProductCooldown(models.Model):
    user          = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='creation_cooldowns')
    until         = models.DateTimeField(db_index=True)
    reason        = models.CharField(max_length=64, blank=True, default='')
    last_triggered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['user', 'until'])]

    @classmethod
    def trigger(cls, user, reason: str = ''):
        now = timezone.now()
        until = now + timedelta(days=3)
        return cls.objects.create(user=user, until=until, reason=reason)

    @classmethod
    def get_state(cls, user):
        rec = cls.objects.filter(user=user).order_by('-until').first()
        if rec and rec.until > timezone.now():
            return True, rec.until
        return False, None

class News(models.Model):
    title = models.CharField(max_length=200)
    content = models.TextField()
    published_at = models.DateTimeField(default=timezone.now)
    is_published = models.BooleanField(default=True)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='news_authored'
    )

    class Meta:
        ordering = ['-published_at']

    def __str__(self):
        return f"{self.published_at:%Y-%m-%d} — {self.title}"

class Visit(models.Model):
    visited = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='visits_received',
        null=False
    )
    visitor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='visits_made',
        null=False
    )
    ip_address = models.CharField(max_length=64, blank=True, null=True)
    visited_at = models.DateTimeField(auto_now_add=True)

class Escrow(models.Model):
    class Status(models.TextChoices):
        HELD      = "HELD", "Held"           # средства зарезервированы (депозит сделан, контент ещё не релизнут)
        RELEASED  = "RELEASED", "Released"   # выплата продавцу
        REFUNDED  = "REFUNDED", "Refunded"   # возврат покупателю
        DISPUTED  = "DISPUTED", "Disputed"   # спор/заморозка

    # Привязка к любому типу заказа (ArtworkOrder / TextProductOrder)
    order_ct   = models.ForeignKey(ContentType, on_delete=models.CASCADE, db_index=True)
    order_id   = models.PositiveIntegerField(db_index=True)
    order_obj  = GenericForeignKey('order_ct', 'order_id')

    # Деньги только в микроединицах для точности и атомарности
    amount_micros = models.BigIntegerField()  # >= 0

    buyer   = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='escrows_as_buyer')
    seller  = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='escrows_as_seller')

    buyer_address  = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    seller_address = models.CharField(max_length=64, blank=True, null=True, db_index=True)

    status  = models.CharField(max_length=16, choices=Status.choices, default=Status.HELD, db_index=True)
    held_at = models.DateTimeField(auto_now_add=True)
    auto_release_at = models.DateTimeField(db_index=True)
    released_at = models.DateTimeField(null=True, blank=True)

    dispute_reason = models.TextField(blank=True, default="")
    dispute_created_at = models.DateTimeField(null=True, blank=True)
    moderator_approved_refund = models.BooleanField(default=False)

    disputed = models.BooleanField(default=False, db_index=True)
    moderator_locked = models.BooleanField(default=False, db_index=True)


    # Идемпотентный ключ для операций (например, при обработке событий/ретраях Celery)
    idempotency_key = models.CharField(max_length=64, unique=True)

    # Ончейн-маркеры (если нужен кросс-линк с контрактом)
    external_order_id = models.CharField(max_length=66, blank=True, null=True, db_index=True)
    deposit_tx  = models.CharField(max_length=66, blank=True, null=True, db_index=True)
    release_tx  = models.CharField(max_length=66, blank=True, null=True, db_index=True)
    refund_tx   = models.CharField(max_length=66, blank=True, null=True, db_index=True)
    refunded_at = models.DateTimeField(null=True, blank=True)

    last_chain_tx_hash = models.CharField(max_length=66, blank=True, null=True, db_index=True)

    class Meta:
        unique_together = [('order_ct', 'order_id')]
        indexes = [
            models.Index(fields=['order_ct', 'order_id', 'status']),
            models.Index(fields=['auto_release_at']),
            models.Index(fields=['external_order_id']),
            models.Index(fields=['status', 'disputed', 'moderator_locked', 'auto_release_at']),
        ]

    def mark_disputed(self, reason: str = ""):
        # не меняем status -> остаётся HELD; включаем флаги блокировки
        self.disputed = True
        self.moderator_locked = True
        if reason:
            self.dispute_reason = reason[:2000]
        self.dispute_created_at = timezone.now()
        self.save(update_fields=["disputed", "moderator_locked", "dispute_reason", "dispute_created_at"])

    def mark_released(self, tx_hash=None, when=None):
        if tx_hash: 
            self.release_tx = tx_hash
        self.status = self.Status.RELEASED
        if when: 
            self.released_at = when
        self.save(update_fields=['release_tx', 'status', 'released_at'])

    def mark_refunded(self, tx_hash=None, when=None):
        if tx_hash:
            self.refund_tx = tx_hash
        self.status = self.Status.REFUNDED
        if when:
            self.refunded_at = when
        self.save(update_fields=['refund_tx', 'status', 'refunded_at'])

    @property
    def seconds_to_autorelease(self) -> int | None:
        if not self.auto_release_at:
            return None
        delta = (self.auto_release_at - timezone.now()).total_seconds()
        return max(int(delta), 0)


class TextProduct(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    
    # Новые поля:
    description = models.TextField(blank=True, null=True, help_text="Краткое описание продукции")
    CATEGORY_CHOICES = [
        ('Аналитика и Исследования', (
            ('geopolitics',          'Геополитика'),
            ('economy_finance',    'Экономика и финансы'),
            ('social_processes',   'Социальные процессы'),
            ('scientific_reviews',      'Научные обзоры'),
        )),
        ('Психология и Человеческое поведение', (
            ('behavioral_psychology',      'Поведенческая психология'),
            ('dark_psychology',    'Темная психология'),
            ('emotional_intelligence',          'Эмоциональный интеллект'),
            ('self_analysis_control',  'Самоанализ и самоконтроль'),
        )),
        ('Философия и Мировоззрение', (
            ('ethics_morality',         'Этика и мораль'),
            ('logic_rationality','Логика и рациональность'),
            ('ontology_thinking',  'Онтология, мышление'),
            ('modern_philosophy','Современная философия'),
        )),
        ('Бизнес и Стратегия', (
            ('entrepreneurship',            'Предпринимательство'),
            ('strategic_analysis',        'Стратегический анализ'),
            ('negotiations_influence',         'Переговоры и влияние'),
            ('marketing_positioning', 'Маркетинг и позиционирование'),
        )),
        ('Искусство и Культура', (
            ('art_history',    'История искусства'),
            ('music_literature',   'Музыка и литература'),
            ('cultural_studies',         'Культурология'),
            ('work_analysis',   'Анализ произведений'),
        )),
        ('Образование и Научпоп', (
            ('educational_texts',   'Обучающие тексты'),
            ('guides_handbooks',   'Справочники, гайды'),
            ('concepts_review',      'Разбор понятий'),
            ('methods_practice',  'Методики и практики'),
        )),
        ('Литературная авторская проза', (
            ('essays',                 'Эссе'),
            ('literary_sketches','Литературные зарисовки'),
            ('mini_novels',          'Мини-романы'),
            ('dialogues_manifestos',    'Диалоги/манифесты'),
        )),
        ('Концепции и Идеи', (
            ('theories_hypotheses',     'Теории и гипотезы'),
            ('new_world_models',     'Новые модели мира'),
            ('manifestos',             'Манифесты'),
            ('ideas_visualization',    'Визуализация идей'),
        )),
        ('Саморазвитие и внутренняя система', (
            ('discipline',             'Дисциплина'),
            ('principles_values',   'Принципы и ценности'),
            ('goal_setting',         'Целеполагание'),
            ('personal_strategy',   'Личная стратегия'),
        )),
        ('Опасные системы и закрытые знания (NSFW)', (
            ('manipulation_systems',           'Манипуляционные системы'),
            ('social_engineering',             'Социальная инженерия'),
            ('control_influence',     'Системы подчинения и влияния'),
            ('disruptive_thinking',        'Подрывные модели мышления'),
            ('mass_control_structures',                'Структуры контроля и управление массами'),
            ('cognitive_traps',               'Когнитивные ловушки и архитектура убеждения'),
        )),
        ('Эротические материалы (NSFW)', (
            ('erotica_psychosexual',      'Эротика и психосексуальные модели'),
            ('provocative_literature',            'Провокационная литература'),
            ('power_domination_psych',    'Психология власти и доминирования'),
            ('intimate_manifestos',         'Интимные философские манифесты'),
            ('corporeality_borders',                   'Телесность и границы восприятия'),
        )),
    ]
    category     = models.CharField(
        max_length=100,
        choices=CATEGORY_CHOICES,
        blank=True, null=True,
        help_text="Категория продукта"
    )

    keywords     = models.CharField(
        max_length=200,
        blank=True, null=True,
        help_text="Теги через #, не больше 10"
    )

    # Сюда будем сохранять зашифрованный контент
    encrypted_content = models.TextField()
    # цена в выбранной валюте, дробная часть до десятых

    price = models.DecimalField(
        max_digits=10,                
        decimal_places=2,            
        validators=[
            MinValueValidator(0),   
            MaxValueValidator(Decimal('10000000.00'))  
        ],
        help_text="Цена от 0 до 10 000 000.00, с точностью до сотых"
    )

    currency     = models.CharField(
        max_length=4,
        choices=Currency.choices,
        default=Currency.USDT
    )

    pages        = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(100)],
        help_text="Количество страниц (1–100)"
    )

    nsfw = models.BooleanField(
        default=False,
        help_text="Отметьте, если содержимое может быть эротическим (NSFW)"
    )


    blocked_until    = models.DateTimeField(null=True, blank=True,
                            help_text="Не продавать до указанной даты")
    block_reason     = models.CharField(max_length=80, blank=True, null=True)


    class Status(models.TextChoices):
       DRAFT     = 'draft',     'Черновик'
       PENDING   = 'pending',   'На рассмотрении'
       APPROVED  = 'approved',  'Одобрен'
       REJECTED  = 'rejected',  'Отклонён'

    def can_edit(self, user) -> bool:
        if getattr(user, "is_staff", False):
            return True
        return self.status != TextProduct.Status.APPROVED

    def clean(self):
        if self.pk:
            original = TextProduct.objects.filter(pk=self.pk).only("status").first()
            if original and original.status == TextProduct.Status.APPROVED:
                raise ValidationError("Редактирование одобренного продукта запрещено.")
        super().clean()


    status           = models.CharField(
       max_length=10,
       choices=Status.choices,
       default=Status.DRAFT,
       db_index=True
    )
    rejection_reason = models.TextField(blank=True, null=True)
    is_active        = models.BooleanField(default=True)  # для остановки/возобновления продажи
    is_deleted       = models.BooleanField(default=False, db_index=True)


    # Флаг, показывающий, одобрен продукт или нет (до проверки он не публикуется)
    created_at = models.DateTimeField(auto_now_add=True)

    # Новые административные оценки (заполняются администратором)
    quality = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Качество (0-100)")
    uniqueness = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Уникальность (0-100)")
    spelling = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Правописание (0-100)")

    def encrypt_content(self, plaintext):
        """
        Шифрует текстовое содержимое с использованием ключа из settings.
        """
        f = Fernet(settings.ENCRYPTION_KEY)
        # Шифруем и возвращаем строку (декодируя из байтов в str)
        return f.encrypt(plaintext.encode('utf-8')).decode('utf-8')

    def decrypt_content(self):
        """
        Расшифровывает и возвращает оригинальный контент.
        Если происходит ошибка, возвращает None.
        """
        f = Fernet(settings.ENCRYPTION_KEY)
        try:
            return f.decrypt(self.encrypted_content.encode('utf-8')).decode('utf-8')
        except Exception:
            return None
        
    def submit_for_review(self):
        self.status = self.Status.PENDING
        self.rejection_reason = ''
        self.save(update_fields=['status','rejection_reason'])

    def mark_approved(self):
        self.status = self.Status.APPROVED
        self.is_active = True
        self.save(update_fields=['status','is_active'])

    def mark_rejected(self, reason: str):
        self.status = self.Status.REJECTED
        self.rejection_reason = reason
        self.is_active = False
        self.save(update_fields=['status','rejection_reason','is_active'])
        CreationQuotaLog.objects.create(user=self.owner, reason=CreationQuotaLog.Reason.REJECTED)
    
    def __str__(self):
        if self.status == self.Status.APPROVED:
            return f"{self.title} (одобрен)"
        else:
            return f"{self.title} (не одобрен)"

class TextProductRating(models.Model):
    product = models.ForeignKey(TextProduct, on_delete=models.CASCADE, related_name='ratings')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    rating = models.PositiveSmallIntegerField(help_text="Оценка от 1 до 5")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('product', 'user')  # Один пользователь может оценить конкретный продукт только один раз

    def __str__(self):
        return f"{self.user.username} rated {self.product.title}: {self.rating}"

class TextProductOrder(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='text_orders')
    product = models.ForeignKey(TextProduct, on_delete=models.CASCADE, related_name='orders')
    purchased_at = models.DateTimeField(default=timezone.now)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=False)
    
    class Meta:
        unique_together = ('user', 'product')  # один продукт — одна покупка

    def __str__(self):
        return f"{self.user.username} купил текст «{self.product.title}»"

    # --- Ончейн-привязка ---
    external_order_id = models.CharField(
        max_length=66, blank=True, null=True, db_index=True, validators=[HEX_66],
        help_text="ID заказа в смарт-контракте (uint256/bytes32/строка)"
    )
    deposit_tx = models.CharField(
        max_length=66, blank=True, null=True, db_index=True, validators=[HEX_66],
        help_text="tx hash депозита в эскроу"
    )
    release_tx = models.CharField(
        max_length=66, blank=True, null=True, db_index=True, validators=[HEX_66],
        help_text="tx hash релиза продавцу"
    )
    refund_tx = models.CharField(
        max_length=66, blank=True, null=True, db_index=True, validators=[HEX_66],
        help_text="tx hash возврата покупателю"
    )
    escrow_timeout_at = models.DateTimeField(
        blank=True, null=True,
        help_text="Срок, после которого возможен таймаут (по данным контракта)"
    )

    # (необязательно, но удобно для индексации)
    onchain_status = models.CharField(
        max_length=24, blank=True, null=True, db_index=True,
        help_text="CREATED/DEPOSITED/RELEASED/REFUNDED/TIMEOUT"
    )


class Artwork(models.Model):

    class Status(models.TextChoices):
        DRAFT    = 'draft',    'Черновик'
        PENDING  = 'pending',  'На модерации'
        APPROVED = 'approved', 'Одобрен'
        REJECTED = 'rejected', 'Отклонён'

    OWNER_CHOICES = [
        ('cg', 'Компьютерная графика'),
        ('painting', 'Живопись'),
        ('drawing', 'Рисунок'),
        ('photo', 'Фотография'),
        ('digital', 'Цифровая иллюстрация'),
    ]

    owner          = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='artworks'
    )
    title = models.CharField(
        max_length=28,
        help_text="Название: от 1 до 28 символов"
    )
    
    # Теги (аналогично TextProduct.keywords)
    keywords = models.CharField(
        max_length=200,
        blank=True, null=True,
        help_text="Теги через #, не больше 10"
    )

    currency = models.CharField(
        max_length=4,
        choices=Currency.choices,
        default=Currency.OSP,
        help_text="Выберите валюту"
    )
    
    description    = models.TextField(blank=True)
    price          = models.DecimalField(max_digits=10, decimal_places=2)

    category       = models.CharField(
        'Категория',
        max_length=20,
        choices=OWNER_CHOICES,
        default='cg'
    )

    # Загруженный оригинал
    original_image = models.ImageField(
        upload_to='artworks/originals/',
        null=True, blank=True
    )

    # Превью с цензурой и водяным знаком
    preview_image  = models.ImageField(
        upload_to='artworks/previews/', 
        blank=True, null=True
    )
    
    # Миниатюра для каталога
    thumbnail      = models.ImageField(
        upload_to='artworks/thumbnails/', 
        blank=True, null=True
    )

    # NSFW-флаг — контент может быть эротическим
    nsfw = models.BooleanField(
        default=False,
        help_text="Отметьте, если содержимое может быть эротическим (NSFW)"
    )


    blocked_until    = models.DateTimeField(null=True, blank=True,
                            help_text="Не продавать до указанной даты")
    block_reason     = models.CharField(max_length=80, blank=True, null=True)

    is_deleted = models.BooleanField(default=False, db_index=True)
    is_approved    = models.BooleanField(default=False)
    created_at     = models.DateTimeField(auto_now_add=True)
    approved_at    = models.DateTimeField(blank=True, null=True)
    rejection_reason = models.TextField(blank=True, null=True)
    
    # JSON-поле: список зон цензуры [{x,y,w,h}, …]
    censored_areas = models.JSONField(default=list, blank=True)
    
    available_copies = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.title

    def can_edit(self, user) -> bool:
        # модераторам можно всегда
        if getattr(user, "is_staff", False):
            return True
        return self.status != self.Status.APPROVED

    def save(self, *args, **kwargs):

        if getattr(self, "currency", None) == "USD":
            self.currency = Currency.USDT

        self.full_clean()

        # Проверяем, изменилось ли изображение, только если объект уже существует
        image_changed = False
        if self.pk:
            try:
                old = Artwork.objects.get(pk=self.pk)
                if old.original_image and self.original_image:
                    image_changed = old.original_image.name != self.original_image.name
            except Artwork.DoesNotExist:
                pass

        # Сначала сохраняем, чтобы original_image точно лежал на диске
        super().save(*args, **kwargs)

        # Генерируем preview, если его нет или исходник изменился
        if self.original_image and (not self.preview_image or image_changed):
            self._generate_preview()

        # Генерируем thumbnail по аналогичной логике
        if self.original_image and (not self.thumbnail or image_changed):
            self._generate_thumbnail()

        # Сохраняем только изменённые поля
        if self.preview_image or self.thumbnail:
            super().save(update_fields=['preview_image', 'thumbnail'])

    def _generate_preview(self):

        img = Image.open(self.original_image.path).convert('RGB')
        draw = ImageDraw.Draw(img)

        # Размытие или прямоугольник
        for a in self.censored_areas:
            x,y,w,h = a['x'],a['y'],a['w'],a['h']
            region = img.crop((x,y,x+w,y+h)).filter(ImageFilter.GaussianBlur(radius=15))
            img.paste(region, (x,y))

        # Водяной знак
        if getattr(settings, 'WATERMARK_PATH', None):
            wm = Image.open(settings.WATERMARK_PATH).convert('RGBA')
            wm_width = img.width // 5
            wm = wm.resize((wm_width, int(wm_width * wm.height / wm.width)))
            img.paste(wm, (img.width - wm_width - 10, img.height - wm.height - 10), wm)

        # Сохраняем
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=85)
        name = f'preview_{self.pk}.jpg'
        self.preview_image.save(name, ContentFile(buf.getvalue()), save=False)

    def _generate_thumbnail(self, size=(200,200)):
        """
        1) Загружаем оригинал.
        2) Делаем thumbnail(size).
        3) Сохраняем в self.thumbnail.
        """
        img = Image.open(self.original_image.path).convert('RGB')
        # обрезаем центр до квадрата
        min_side = min(img.width, img.height)
        left   = int((img.width  - min_side) / 2)
        top    = int((img.height - min_side) / 2)
        right  = left + min_side
        bottom = top  + min_side
        img = img.crop((left, top, right, bottom))
        # точно в размер
        img = img.resize(size, Image.Resampling.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=75)
        name = f'thumb_{self.pk}.jpg'
        self.thumbnail.save(name, ContentFile(buf.getvalue()), save=False)

    @property
    def is_listed(self) -> bool:
        # 1) только одобренные
        if self.status != self.Status.APPROVED:
            return False
        # 2) админ/автор не снимал с продажи
        if not self.is_active:
            return False
        # 3) есть тираж
        if (self.available_copies or 0) <= 0:
            return False
        # 4) нет активной блокировки по времени
        if self.blocked_until and self.blocked_until > timezone.now():
            return False
        return True

    def get_cover_url(self):
        """
        Приоритет: явная цензурная обложка → явная оригинальная →
        первая страница (цензурная) → первая страница (оригинал) → placeholder.
        """
        # 1) явная цензурная обложка
        cover_c = getattr(self, 'cover_censored_image', None)
        if cover_c:
            try:
                return cover_c.url
            except Exception:
                pass

        # 2) явная оригинальная обложка (thumbnail / cover_image)
        # подстрой под твои поля: thumbnail или cover_image
        for field in ('thumbnail', 'cover_image'):
            f = getattr(self, field, None)
            if f:
                try:
                    return f.url
                except Exception:
                    pass

        # 3) первая страница: цензурная, затем оригинал
        rel = getattr(self, 'pages', None) or getattr(self, 'artworkpage_set', None)
        first_page = rel.order_by('order', 'id').first() if rel else None
        if first_page:
            ci = getattr(first_page, 'censored_image', None)
            if ci:
                try:
                    return ci.url
                except Exception:
                    pass
            oi = getattr(first_page, 'image', None)
            if oi:
                try:
                    return oi.url
                except Exception:
                    pass

        # 4) заглушка
        return static('images/placeholder.png')


    def get_cover_image_url(self):
        return self.get_cover_url()

    def submit_for_review(self):
        # перед отправкой убедимся, что NSFW имеет зоны цензуры (валидация формы уже есть,
        # но дублируем бизнес-правило на уровне модели)
        if self.nsfw and not self.censored_areas:
            from django.core.exceptions import ValidationError
            raise ValidationError("Для NSFW-артворков необходимо добавить области цензуры.")
        self.is_active = False
        self.status = self.Status.PENDING
        self.rejection_reason = ""
        self.save(update_fields=["status", "is_active", "rejection_reason"])

    def mark_approved(self):
        from django.utils import timezone
        self.status = self.Status.APPROVED
        self.is_active = True
        self.approved_at = timezone.now()
        # поле is_approved у тебя уже есть — оно дублирует статус; поддержим совместимость
        self.is_approved = True
        self.save(update_fields=["status", "is_active", "approved_at", "is_approved"])

    def mark_rejected(self, reason: str):
        self.status = self.Status.REJECTED
        self.rejection_reason = (reason or "")[:2000]
        self.is_active = False
        self.is_approved = False
        self.save(update_fields=["status", "rejection_reason", "is_active", "is_approved"])
        CreationQuotaLog.objects.create(user=self.owner, reason=CreationQuotaLog.Reason.REJECTED)

    def clean(self):
        super().clean()

        if self.currency == 'USD':
            self.currency = Currency.USDT

        if self.currency == Currency.USDT and self.price < Decimal('5.00'):
            raise ValidationError({'price': 'Минимальная цена для USDT — 5 USDT.'})

        if not self.pk:
            return
        original = type(self).objects.filter(pk=self.pk).only(
            "status", "is_active", "blocked_until", "available_copies"
        ).first()
        if not original:
            return

        if original.status == self.Status.APPROVED:
            allowed_ok = (
                self.is_active != original.is_active or
                self.blocked_until != original.blocked_until or
                self.available_copies != original.available_copies
            )
            if not allowed_ok:
                raise ValidationError("Редактирование одобренного артворка запрещено.")

    class Meta:
        indexes = [
            models.Index(fields=["status", "is_active", "available_copies"]),
            models.Index(fields=["blocked_until"]),
            models.Index(fields=["currency", "price"]),
            models.Index(fields=["approved_at", "id"]),
            models.Index(fields=["category", "nsfw"]),
        ]
        constraints = [
            CheckConstraint(
                check=Q(available_copies__gte=0),
                name="artwork_available_copies_nonnegative",
            ),
        ]


    status    = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.DRAFT
    )
    created   = models.DateTimeField(auto_now_add=True)
    updated   = models.DateTimeField(auto_now=True)

class ArtworkRating(models.Model):
    artwork = models.ForeignKey(Artwork, on_delete=models.CASCADE, related_name='ratings')
    user    = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    rating  = models.PositiveSmallIntegerField(help_text="Оценка от 1 до 5")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('artwork', 'user')


class Purchase(models.Model):
    user         = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='purchases')
    artwork      = models.ForeignKey(Artwork, on_delete=models.CASCADE, related_name='purchases')
    purchased_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} → {self.artwork.title} @ {self.purchased_at}"

class ArtworkImage(models.Model):
    artwork    = models.ForeignKey(Artwork, on_delete=models.CASCADE, related_name='pages')
    image      = models.ImageField(upload_to='artworks/series/')
    censored_image = models.ImageField(
        upload_to='artworks/censored/',
        blank=True, null=True
    )
    order          = models.PositiveIntegerField(default=0)


    class Meta:
        ordering = ['order']
        unique_together = [('artwork', 'order')]

    def __str__(self):
        return f'{self.artwork.title} — страница {self.order}'

class ArtworkOrder(models.Model):
    class Status(models.TextChoices):
        PENDING  = 'PENDING',  'Ожидание'
        RELEASED = 'RELEASED', 'Выполнено'
        REFUNDED = 'REFUNDED', 'Возврат'
        COMPLETED  = 'COMPLETED',   'Завершён' 

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='artwork_orders'
    )
    artwork = models.ForeignKey(
        'Artwork',
        on_delete=models.CASCADE,
        related_name='orders'
    )
    tx_hash = models.CharField(
        max_length=66,
        help_text="hash транзакции createOrder"
    )
    amount = models.DecimalField(
        max_digits=30,
        decimal_places=0,
        help_text="Сумма в базовой единице (wei, satoshi и т.д.)"
    )
    escrow_released = models.BooleanField(
        default=False,
        help_text="Флаг, что эскроу было освобождено"
    )
    released_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Когда эскроу было освобождено"
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Время создания записи"
    )

    # --- Ончейн-привязка ---
    external_order_id = models.CharField(
        max_length=66, blank=True, null=True, db_index=True, validators=[HEX_66],
        help_text="ID заказа в смарт-контракте (uint256/bytes32/строка)"
    )
    deposit_tx = models.CharField(
        max_length=66, blank=True, null=True, db_index=True, validators=[HEX_66],
        help_text="tx hash депозита в эскроу"
    )
    release_tx = models.CharField(
        max_length=66, blank=True, null=True, db_index=True, validators=[HEX_66],
        help_text="tx hash релиза продавцу"
    )
    refund_tx = models.CharField(
        max_length=66, blank=True, null=True, db_index=True, validators=[HEX_66],
        help_text="tx hash возврата покупателю"
    )
    escrow_timeout_at = models.DateTimeField(
        blank=True, null=True,
        help_text="Срок, после которого возможен таймаут (по данным контракта)"
    )

    # (необязательно, но удобно для индексации)
    onchain_status = models.CharField(
        max_length=24, blank=True, null=True, db_index=True,
        help_text="CREATED/DEPOSITED/RELEASED/REFUNDED/TIMEOUT"
    )

    class Meta:
        unique_together = ('user', 'artwork', 'external_order_id')

class Gift(models.Model):
    sender    = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='gifts_sent')
    recipient = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='gifts_received')
    # Связываем подарок с любым объектом: текст, урок или арт
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id    = models.CharField(max_length=36)  # UUID или int
    content_object = GenericForeignKey('content_type', 'object_id')

    message   = models.TextField(blank=True, help_text="Ваше сообщение-поздравление")
    sent_at   = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"Подарок от {self.sender.username} к {self.recipient.username}"

class Lesson(models.Model):
    title = models.CharField(max_length=100)
    description = models.TextField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    start_date = models.DateTimeField()
    teacher = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='lessons_taught')
    students = models.ManyToManyField(CustomUser, related_name='lessons_enrolled', blank=True)

    def __str__(self):
        return self.title

class Enrollment(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='enrollments')
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name='enrollments')
    enrolled_at = models.DateTimeField(default=timezone.now)
    paid = models.BooleanField(default=False)

    class Meta:
        unique_together = ('user', 'lesson')  # Один пользователь — один раз

    def __str__(self):
        return f"{self.user.username} → {self.lesson.title}"


class Notification(models.Model):
    user      = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                 related_name='notifications')
    message   = models.TextField()
    link      = models.URLField(blank=True)
    is_read   = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Уведомление для {self.user.username}: {self.message[:30]}..."


class RefundRequest(models.Model):
    ORDER_TYPES = [
        ('artwork', 'ArtworkOrder'),
        ('text', 'TextProductOrder'),
    ]
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    order_type = models.CharField(max_length=10, choices=ORDER_TYPES)
    order_id = models.PositiveIntegerField()
    reason = models.TextField()
    status = models.CharField(max_length=10, choices=[
        ('pending', 'Ожидает'),
        ('approved', 'Одобрена'),
        ('rejected', 'Отклонена')
    ], default='pending')
    moderator_comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.get_order_type_display()} #{self.order_id} от {self.user.username}"


class PaymentOrder(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Ожидает оплаты'),
        ('paid',    'Оплачен'),
        ('failed',  'Отменён'),
    ]

    user       = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='payment_orders'
    )
    artwork    = models.ForeignKey(
        'Artwork',
        on_delete=models.CASCADE,
        related_name='payment_orders'
    )
    amount     = models.DecimalField(
        max_digits=20,
        decimal_places=8,
        validators=[MinValueValidator(Decimal('0.00000001'))]
    )
    currency   = models.CharField(
        max_length=4,
        choices=Currency.choices,
        default=Currency.USDT
    )
    status     = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='pending',
        db_index=True
    )
    tx_hash    = models.CharField(
        max_length=66,
        blank=True,
        help_text="Хэш транзакции on-chain после оплаты"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = ('user', 'artwork', 'status')

    def __str__(self):
        return f"Order#{self.pk} {self.artwork.title} for {self.amount} {self.currency}"


class ChainCursor(models.Model):
    class Network(models.TextChoices):
        ETHEREUM_SEPOLIA = "ethereum-sepolia", "Ethereum Sepolia"
        ETHEREUM_MAINNET = "ethereum-mainnet", "Ethereum Mainnet"

    network     = models.CharField(max_length=64, choices=Network.choices)
    contract    = models.CharField(max_length=42, help_text="Адрес контракта (checksum, 0x...)", db_index=True)
    last_block  = models.BigIntegerField(default=0, help_text="Последний обработанный блок (включительно)")
    updated_at  = models.DateTimeField(auto_now=True)
    note        = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        unique_together = (("network", "contract"),)
        indexes = [
            models.Index(fields=["network", "contract"]),
        ]

    def __str__(self):
        return f"{self.network}:{self.contract} @ {self.last_block}"


class Dispute(models.Model):
    class Status(models.TextChoices):
        OPENED   = "OPENED", "Открыт"
        RESOLVED = "RESOLVED", "Закрыт"

    escrow = models.OneToOneField('Escrow', on_delete=models.CASCADE, related_name='dispute')
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='opened_disputes')
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPENED)

    reason = models.TextField(blank=True)
    evidence_url = models.URLField(blank=True)

    moderator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='moderated_disputes')
    moderator_decision = models.CharField(max_length=32, blank=True)  # "RELEASE" | "REFUND"
    decision_tx = models.CharField(max_length=80, blank=True)         # txHash (для ончейн)
    decided_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def is_open(self):
        return self.status == self.Status.OPENED

    def __str__(self):
        return f"Dispute #{self.pk} for escrow {self.escrow_id}"

class OspTopUpOrder(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING"
        CONFIRMED = "CONFIRMED"
        CANCELED = "CANCELED"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="osp_topups")
    amount_usd_micros = models.BigIntegerField()  # сколько пользователь хочет внести
    reference = models.CharField(max_length=64, db_index=True)  # тот же reference, что ты уже создаёшь
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    def mark_confirmed(self):
        self.status = self.Status.CONFIRMED
        self.confirmed_at = timezone.now()
        self.save(update_fields=["status", "confirmed_at"])


class VIPPlan(models.Model):
    class Level(models.TextChoices):
        GOLD   = "GOLD",   "Gold VIP"
        STAR   = "STAR",   "Star VIP"
        GALAXY = "GALAXY", "Galaxy VIP"

    code = models.CharField(max_length=50, unique=True)
    title = models.CharField(max_length=100)
    # ЦЕНА теперь в OSP-микросах (1 OSP = 1_000_000 μOSP)
    price_osp_micros = models.BigIntegerField()

    # Срок действия
    duration_days = models.PositiveIntegerField(default=30)
    is_active = models.BooleanField(default=True)

    # Характеристики уровня
    level = models.CharField(
        max_length=20,
        choices=[('GOLD', 'Gold'), ('STAR', 'Star'), ('GALAXY', 'Galaxy')],
        default='GOLD'
    )

    popularity_boost = models.IntegerField(default=0)
    # Квоты создания продуктов
    daily_quota = models.PositiveIntegerField(default=0)       
    min_interval_days = models.PositiveIntegerField(default=0) 

    def __str__(self):
        return f"{self.title} — {self.price_osp_micros/1_000_000:.6f} OSP"



class VIPSubscription(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="vip_subscriptions")
    plan = models.ForeignKey(VIPPlan, on_delete=models.PROTECT)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def active(self):
        return self.end_at >= timezone.now()

    @staticmethod
    def grant_or_extend(user, plan):
        now = timezone.now()
        last = (
            VIPSubscription.objects
            .filter(user=user)
            .order_by('-end_at')
            .first()
        )
        if last and last.end_at >= now:
            # продлеваем текущую
            start = last.end_at
        else:
            start = now
        end = start + timedelta(days=plan.duration_days)
        return VIPSubscription.objects.create(user=user, plan=plan, start_at=start, end_at=end)


class VIPGiftRecord(models.Model):
    """
    Аудит «подарочных» VIP-выдач (без списания денег).
    """
    user       = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="vip_gifts")
    plan       = models.ForeignKey('VIPPlan', on_delete=models.PROTECT, related_name="gift_records")
    reason     = models.CharField(max_length=255, blank=True)
    starts_at  = models.DateTimeField()
    ends_at    = models.DateTimeField(null=True, blank=True)  # None = бессрочно (не рекомендуется)
    moderator  = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="vip_gifts_made")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class CreationQuotaLog(models.Model):
    class Reason(models.TextChoices):
        TEXT_CREATED    = "TEXT_CREATED"
        TEXT_SUBMITTED  = "TEXT_SUBMITTED"
        ART_CREATED     = "ART_CREATED"
        ART_SUBMITTED   = "ART_SUBMITTED"
        REJECTED        = "REJECTED"  # модерация отклонила/на доработку

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="creation_logs")
    reason = models.CharField(max_length=20, choices=Reason.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["user", "created_at"])]


class LedgerEntry(models.Model):
    class Side(models.TextChoices):
        DEBIT  = "DEBIT"
        CREDIT = "CREDIT"

    class Kind(models.TextChoices):
        TOPUP_OSP_SOFT   = "TOPUP_OSP_SOFT"    # мгновенный зачёт OSP (мягкий, но у нас считается финальным)
        PURCHASE_VIP     = "PURCHASE_VIP"
        PURCHASE_PRODUCT = "PURCHASE_PRODUCT"
        ESCROW_HOLD      = "ESCROW_HOLD"
        ESCROW_RELEASE   = "ESCROW_RELEASE"
        ESCROW_REFUND    = "ESCROW_REFUND"
        REVERSAL         = "REVERSAL"
        SALE_FEE         = "SALE_FEE"
        SALE_INCOME      = "SALE_INCOME"

    user    = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ledger_entries")
    wallet  = models.ForeignKey("users.CryptoWallet", on_delete=models.CASCADE, related_name="ledger_entries")
    side    = models.CharField(max_length=6, choices=Side.choices)
    kind    = models.CharField(max_length=32, choices=Kind.choices)
    currency= models.CharField(max_length=4)  # 'OSP' / 'USDT' и т.п.
    amount_micros = models.BigIntegerField()  # всегда >0
    balance_after_micros = models.BigIntegerField()  # слепок баланса после записи
    reference = models.CharField(max_length=128, blank=True, db_index=True)  # 'vip:vip_month' или UUID
    external_tx_hash = models.CharField(max_length=66, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["reference"], name="idx_ledger_reference"),
            models.Index(fields=["user", "wallet", "created_at"]),
            models.Index(fields=["reference"]),
            models.Index(fields=["kind", "created_at"]),
        ]

    def __str__(self):
        sign = "+" if self.side == self.Side.CREDIT else "-"
        return f"[{self.created_at:%Y-%m-%d %H:%M}] {self.kind} {sign}{self.amount_micros}μ {self.currency}"