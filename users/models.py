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
from django.urls import reverse
from django.shortcuts import redirect
import uuid
import logging
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.core.validators import RegexValidator

logger = logging.getLogger(__name__)

from django.urls import reverse
from django.shortcuts import redirect


HEX_66 = RegexValidator(r'^0x[a-fA-F0-9]{64}$', 'Ожидается 0x + 64 hex-символа.')

class LoginRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Получаем URL-ы по их именам из urls.py
        home_url = reverse('home')

        # Логируем для отладки (по желанию)
        logger.debug(f"REQUEST PATH: {request.path}")
        logger.debug(f"HOME_URL: {home_url}")

        # Если пользователь не авторизован...
        if not request.user.is_authenticated:
            # И при этом он зашёл не на главную страницу и не на статические или медиа файлы...
            if (
                request.path != home_url and not request.path.startswith('/static/') and not request.path.startswith('/media/')
            ):
                # Перенаправляем на страницу home, где будет модальное окно для входа
                logger.debug("Redirecting non-authenticated user to home (login modal)...")
                return redirect('home')

        # Если пользователь авторизован ИЛИ URL в списке исключений — пропускаем запрос дальше
        response = self.get_response(request)
        return response

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
    
    def full_name(self):
        full = f"{self.first_name} {self.last_name}".strip()
        return full or self.username

    def get_full_name(self):
        return self.full_name

    def get_short_name(self):
        return self.first_name or self.username
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



class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    bio = models.CharField(max_length=150, blank=True, null=True, verbose_name="Описание профиля")
    background_color = models.CharField(max_length=20, default='#3498db')
    popularity = models.IntegerField(default=0)

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
    last_scanned_block = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)


    class Meta:
        unique_together = ('user', 'currency')

class CryptoTransaction(models.Model):
    wallet      = models.ForeignKey(CryptoWallet, on_delete=models.CASCADE, related_name='txs')
    tx_type     = models.CharField(max_length=20, choices=[('deposit','Deposit'),('escrow','Escrow'),
                                                            ('release','Release'),('purchase','Purchase')])
    amount      = models.DecimalField(max_digits=30, decimal_places=18)
    tx_hash     = models.CharField(max_length=66, blank=True)  # hash on-chain
    reference   = models.CharField(max_length=100, blank=True) # внешние ID
    created_at  = models.DateTimeField(auto_now_add=True)




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

    def save(self, *args, **kwargs):
        """
        Переопределяем save, чтобы после загрузки original_image:
        1) Сгенерировать preview_image с размытием/блоками и водяным знаком.
        2) Сгенерировать thumbnail из оригинала.
        """
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

    def get_cover_image_url(self):

        if self.thumbnail:
            return self.thumbnail.url
        first_page = self.pages.order_by('order').first()
        if first_page:
            return first_page.image.url
        from django.templatetags.static import static
        return static('images/placeholder.png')

    def clean(self):
        super().clean()
        if self.currency == Currency.USDT and self.price < Decimal('5.00'):
            raise ValidationError({
                'price': 'Минимальная цена для USDT — 5 USDT.'
            })
        

    status    = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.DRAFT
    )
    created   = models.DateTimeField(auto_now_add=True)
    updated   = models.DateTimeField(auto_now=True)

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


