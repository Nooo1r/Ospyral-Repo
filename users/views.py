from django.shortcuts import render, redirect, get_object_or_404, resolve_url
from django.contrib.auth import login, logout, get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_http_methods
from django.views.decorators.cache import never_cache
import requests, datetime as dt
from django.core import signing
from django.utils import timezone

from django.core.mail import EmailMessage
from django.core.paginator import Paginator
from django.contrib.contenttypes.models import ContentType
from django.utils.crypto import get_random_string
from eth_account.messages import encode_defunct
from django.db.models.expressions import OrderBy
from django.conf import settings
from django.core.files.base import ContentFile
from django.contrib import messages
import logging, base64, json, uuid
from django.db.models import Count, Avg, Q, F, Prefetch, Value
from django.views.decorators.csrf import csrf_protect
from decimal import Decimal, ROUND_DOWN, InvalidOperation
from django.db.models.functions import Coalesce
from django.utils.http import url_has_allowed_host_and_scheme
from .money import usd_to_micros, settle_vip_revenue_to_platform, settle_sale_with_platform_fee_osp
from .utils import make_order_id_hex
from django.db import transaction
from django.http import HttpResponse, FileResponse
from django.contrib.sessions.models import Session
from datetime import datetime, timedelta
import re
import os
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .accounting import book_osp
from PIL import Image
from io import BytesIO
from .crypto.web3 import w3
from web3 import Web3
import zipfile
from .purchase import process_purchase

from django.http import (
    HttpResponseNotFound,
    JsonResponse, 
    HttpResponseRedirect, 
    HttpResponseForbidden, 
    HttpResponseBadRequest
)

from django.urls import reverse

import hashlib
import random, string, requests

from .forms import (
    TextProductForm, ConfirmPurchaseForm,
    ProfileEditForm,
    CustomUserCreationForm,
    CustomAuthenticationForm,
    LessonForm, DisputeOpenForm,
    ArtworkForm, ArtworkImageForm,
    TextProductForm, ProfileThemeForm,
)

from .models import (
    EmailVerification, TextProductOrder, 
    CryptoWallet, CryptoTransaction, 
    Currency, ArtworkOrder,
    ArtworkImage, CustomUser,
    Visit, Profile,
    Artwork, Lesson, TextProduct,
    TextProductRating, Notification,
    Escrow, Dispute, Purchase,
    VIPPlan, VIPSubscription, LedgerEntry,
    UserProductCooldown, News,
    CreationQuotaLog, ArtworkRating,
)

CustomUser = get_user_model()
logger = logging.getLogger(__name__)

SEPARATOR = "\n\n---PAGEBREAK---\n\n"

TX_HASH_RE  = re.compile(r"^0x[a-fA-F0-9]{64}$")
ORDER_ID_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")

USDT_DECIMALS = getattr(settings, "USDT_DECIMALS", 6)
CHAIN_ID_EXPECTED = int(getattr(settings, "CHAIN_ID", 0))

TRUST_COOKIE_NAME = "osp_trusted"
TRUST_TTL_DAYS = 90
LOGIN_FAIL_THRESHOLD = 3 
STEPUP_ON_UNTRUSTED = True


def to_base_units(amount: Decimal, decimals: int) -> int:
    q = Decimal(10) ** decimals
    return int((amount * q).to_integral_value(rounding=ROUND_DOWN))

TOKEN_DECIMALS = {
    "USDT": int(getattr(settings, "USDT_TOKEN_DECIMALS", 6)),  
    "OSP":  int(getattr(settings, "OSP_TOKEN_DECIMALS", 18)),
}

TOKEN_ADDRESS = {
    "USDT": getattr(settings, "USDT_TOKEN_ADDRESS", None),
    "OSP":  getattr(settings, "OSP_CONTRACT_ADDRESS", None),
}

MAX_ATTEMPTS = 5
LOCK_MINUTES = 10

ETH_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
SIG_RE         = re.compile(r"^0x[a-fA-F0-9]{130}$")


def test_form(request):
    form = CustomUserCreationForm()
    return render(request, 'test_form.html', {'form': form})



def home_view(request):
    show_login_modal = True  # Показ модального окна входа
    show_register_modal = False  # Показ модального окна регистрации

    context = {
        'show_login_modal': show_login_modal,
        'show_register_modal': show_register_modal,
    }
    return render(request, 'home.html', context)


def anonymous_required(view_func, redirect_field_name=None, login_url='dashboard'):
    return user_passes_test(
        lambda u: not u.is_authenticated,
        login_url=login_url,
        redirect_field_name=redirect_field_name,
    )(view_func)


@anonymous_required
def register_view(request):

    if request.method == 'POST':
        form = CustomUserCreationForm(request.POST)
        if form.is_valid():
            # 1) Проверяем Turnstile
            token = form.cleaned_data['cf_turnstile_response']
            resp = requests.post(
                'https://challenges.cloudflare.com/turnstile/v0/siteverify',
                data={
                    'secret': settings.CF_TURNSTILE_SECRET_KEY,
                    'response': token,
                    'remoteip': request.META.get('REMOTE_ADDR'),
                }
            ).json()
            if not resp.get('success'):
                messages.error(request, "Проверка «я не робот» не пройдена.")
                return render(request, 'registration/register.html', {'form': form, 'site_key': settings.CF_TURNSTILE_SITE_KEY})

            # 2) Создаём пользователя, но не активируем его
            user = form.save(commit=False)
            user.is_active = False
            user.save()

            # 3) Генерируем код и сохраняем
            code = ''.join(random.choices(string.digits, k=6))
            expires = timezone.now() + timedelta(minutes=30)
            EmailVerification.objects.create(
                user=user,
                code=code,
                expires_at=expires
            )

            # 4) Отправляем email с кодом
            EmailMessage(
                subject="Код подтверждения для Ospyral",
                body=f"Ваш код подтверждения: {code}\nОн действителен до {expires.strftime('%H:%M %d.%m.%Y')}.",
                to=[user.email]
            ).send()

            # 5) Сохраняем во временной сессии и редиректим на страницу ввода кода
            request.session['pending_user_id'] = user.id
            return redirect('verify_email')
    else:
        form = CustomUserCreationForm()

    return render(request, 'registration/register.html', {
        'form': form,
        'site_key': settings.CF_TURNSTILE_SITE_KEY
    })


def verify_email(request):
    user_id = request.session.get('pending_user_id')
    if not user_id:
        return redirect('register')
    user = get_object_or_404(CustomUser, pk=user_id)

    # блокировка при переборе
    locked_until = request.session.get('verify_lock_until')
    if locked_until and timezone.now().timestamp() < locked_until:
        messages.error(request, "Слишком много неверных попыток. Попробуйте позже.")
        return render(request, 'registration/verify_email.html')

    if request.method == 'POST':
        code = (request.POST.get('code') or '').strip()

        # счётчик попыток в сессии
        tries = int(request.session.get('verify_tries', 0)) + 1
        request.session['verify_tries'] = tries
        if tries > MAX_ATTEMPTS:
            request.session['verify_lock_until'] = (timezone.now() + timedelta(minutes=LOCK_MINUTES)).timestamp()
            messages.error(request, "Слишком много неверных попыток. Повторите позже.")
            return render(request, 'registration/verify_email.html', status=429)

        ev = EmailVerification.objects.filter(
            user=user, code=code, expires_at__gte=timezone.now()
        ).first()

        if ev:
            # Активируем и чистим все коды этого пользователя (одноразовость)
            user.is_active = True
            user.is_email_verified = True
            user.save(update_fields=['is_active', 'is_email_verified'])

            EmailVerification.objects.filter(user=user).delete()
            request.session.pop('pending_user_id', None)
            request.session.pop('verify_tries', None)
            request.session.pop('verify_lock_until', None)

            login(request, user, backend='users.backends.EmailBackend')
            messages.success(request, "Email подтверждён, вы вошли в систему.")
            return redirect('dashboard')
        else:
            messages.error(request, "Неверный или просроченный код.")

    return render(request, 'registration/verify_email.html')


def _mask_email(email: str) -> str:
    try:
        name, dom = email.split("@", 1)
        return (name[0] + "***@" + dom) if name else "***@" + dom
    except Exception:
        return "***"

def _has_trusted_cookie(request, user_id: int) -> bool:
    raw = request.COOKIES.get(TRUST_COOKIE_NAME, "")
    if not raw:
        return False
    try:
        data = signing.loads(raw, key=settings.SECRET_KEY, max_age=TRUST_TTL_DAYS*24*3600)
        return int(data.get("uid", 0)) == int(user_id)
    except Exception:
        return False

def _make_trusted_cookie(user_id: int):
    payload = {"uid": int(user_id), "ts": timezone.now().timestamp()}
    val = signing.dumps(payload, key=settings.SECRET_KEY)
    expires = dt.datetime.utcnow() + dt.timedelta(days=TRUST_TTL_DAYS)
    return val, expires



@anonymous_required
def login_view(request):
    # если уже залогинен и не просит «переключить»
    if request.user.is_authenticated and request.GET.get('switch') == '1':
        logout(request)
    elif request.user.is_authenticated and request.method == "GET":
        return redirect('dashboard')

    # лимит + капча (оставляем как было)
    fails = int(request.session.get('login_fails', 0))
    show_captcha = fails >= LOGIN_FAIL_THRESHOLD

    def ctx(form, *, show_captcha_val=None):
        return {
            'form': form,
            'show_captcha': show_captcha if show_captcha_val is None else show_captcha_val,
            'site_key': getattr(settings, 'CF_TURNSTILE_SITE_KEY', ''),
            # чтобы шаблон не ругался, даже если остался код:
            'challenge_required': False,
            'masked_email': "",
        }

    if request.method == 'POST':
        form = CustomAuthenticationForm(request, data=request.POST)

        # если включили Turnstile — проверяем
        if show_captcha:
            token = request.POST.get('cf_turnstile_response', '')
            ok = False
            if token:
                try:
                    import requests
                    resp = requests.post(
                        'https://challenges.cloudflare.com/turnstile/v0/siteverify',
                        data={
                            'secret': settings.CF_TURNSTILE_SECRET_KEY,
                            'response': token,
                            'remoteip': request.META.get('REMOTE_ADDR'),
                        },
                        timeout=5,
                    ).json()
                    ok = bool(resp.get('success'))
                except Exception:
                    ok = False
            if not ok:
                messages.error(request, "Проверка «я не робот» не пройдена.")
                return render(request, 'registration/login.html',
                              ctx(form, show_captcha_val=True), status=400)

        if form.is_valid():
            user = form.get_user()

            # бан оставляем
            if getattr(user, "is_banned", False):
                from .views import banned_page  # если в этом же модуле — убери импорт
                if user.ban_until and timezone.now() >= user.ban_until:
                    user.is_banned = False
                    user.ban_reason = ""
                    user.ban_until = None
                    user.save(update_fields=["is_banned", "ban_reason", "ban_until"])
                else:
                    return banned_page(request)

            # НИЧЕГО больше не требуем: ни верификации email, ни 2-го шага
            login(request, user)
            request.session['login_fails'] = 0
            messages.success(request, f"Вы успешно вошли как {user.username}")
            return redirect('dashboard')

        # пароль не прошёл
        request.session['login_fails'] = fails + 1
        return render(request, 'registration/login.html',
                      ctx(form, show_captcha_val=((fails + 1) >= LOGIN_FAIL_THRESHOLD)),
                      status=400)

    # GET
    form = CustomAuthenticationForm(request=request)
    return render(request, 'registration/login.html', ctx(form))


COOLDOWN_ENABLED = True  # на будущее, чтобы быстро отключить
def check_creation_cooldown(user):
    if getattr(user, 'is_staff', False):
        return (False, None)   # админам — без ограничений
    # TODO: позже: проверка активного VIP-плана
    return UserProductCooldown.get_state(user)


def banned_page(request):
    """
    Универсальная страница бана для:
    - забаненных пользователей (is_banned)
    - отпечатков (IP/UA/device_id), определённых middleware
    """
    user = request.user if request.user.is_authenticated else None
    until = getattr(user, "ban_until", None) if user else None
    reason = getattr(user, "ban_reason", "") if user else "Нарушение правил"
    # seconds_left для таймера
    seconds_left = max(0, int((until - timezone.now()).total_seconds())) if until else None
    return render(request, "banned.html", {
        "reason": reason,
        "ban_until": until,
        "seconds_left": seconds_left,
    }, status=403)


@login_required
def create_product_hub(request):
    blocked, until = check_creation_cooldown(request.user)
    remaining_seconds = 0
    if blocked and until:
        remaining_seconds = max(0, int((until - timezone.now()).total_seconds()))

    context = {
        'blocked': blocked,
        'until': until,
        'remaining_seconds': remaining_seconds,
    }
    return render(request, 'create_product_hub.html', context)


@login_required
def dashboard(request):
    # 1) TOP-3 пользователей по числу продаж (подтягиваем profile одним запросом)
    top_sales_users = (
        CustomUser.objects.select_related('profile')
        .annotate(
            text_sales=Count('text_orders', distinct=True),
            art_sales=Count('artwork_orders', distinct=True),
        )
        .annotate(sales_count=F('text_sales') + F('art_sales'))
        .order_by('-sales_count')[:3]
    )

    # 2) TOP-3 самых популярных пользователей (NULL → 0, и тоже select_related)
    top_popular_users = (
        CustomUser.objects.select_related('profile')
        .annotate(popularity=Coalesce(F('profile__popularity'), Value(0)))
        .order_by('-popularity')[:3]
    )

    # 3) TOP-3 самых продаваемых текстовых продуктов
    top_texts = TextProduct.objects.select_related('owner').filter(
        status=TextProduct.Status.APPROVED,
        is_active=True
    ).annotate(
        sales=Count('orders')
    ).order_by('-sales')[:3]

    # 4) TOP-3 самых продаваемых артворков
    top_artworks = (Artwork.objects.select_related('owner')
        .filter(status=Artwork.Status.APPROVED, is_active=True)
        .annotate(sales=Count('orders'))
        .order_by('-sales')[:3]
    )

    # 5) Общее число покупок (тексты + артворки)
    orders_count = TextProductOrder.objects.count() + ArtworkOrder.objects.count()

    # 6) «Пользователи в сети» — уникальные визитёры за последние 10 минут
    sessions = Session.objects.filter(expire_date__gte=timezone.now())
    user_ids = {s.get_decoded().get('_auth_user_id') for s in sessions if s.get_decoded().get('_auth_user_id')}
    visits_count = len(user_ids)

    # 7) Сколько уроков создал текущий пользователь
    #    (может подстроить под свою метрику)
    products_total = (
        TextProduct.objects.filter(status=TextProduct.Status.APPROVED, is_active=True).count()
        +
        Artwork.objects.filter(status=Artwork.Status.APPROVED, is_active=True)
                       .filter(Q(available_copies__gt=0) | Q(available_copies__isnull=True)).count()
    )

    news_items = News.objects.filter(is_published=True).order_by('-published_at')[:10]

    unread_notifications = request.user.notifications.filter(is_read=False)

    # Не спамим сообщением на каждый GET; показываем только если есть query-параметр
    if request.GET.get('welcome') == '1':
        messages.success(request, 'Добро пожаловать на панель управления!')

    return render(request, 'dashboard.html', {
        'top_sales_users': top_sales_users,
        'top_popular_users': top_popular_users,
        'top_popular_products': list(top_texts) + list(top_artworks),
        'orders_count': orders_count,
        'visits_count': visits_count,
        'product_count': products_total,
        'news_items': news_items,
        'unread_notifications': unread_notifications,
    })


@login_required
def artwork_list(request):
    artworks = Artwork.objects.all()
    return render(request, 'artworks.html', {'artworks': artworks})



@login_required
def lesson_list(request):
    lessons = Lesson.objects.all()
    return render(request, 'lessons.html', {'lessons': lessons})


def hash_ip(ip_address: str) -> str:
    return hashlib.sha256(ip_address.encode('utf-8')).hexdigest()


def rules_overview(request):
    return render(request, 'rules_overview.html')


def about_us(request):
    return render(request, 'about_us.html')

@login_required
@require_POST
def logout_view(request):
    """
    Безопасный logout по POST. Делает редирект на безопасный next / реферер / home.
    """
    logout(request)
    messages.info(request, "Вы успешно вышли из системы.")

    # Порядок приоритета: hidden next -> Referer -> 'home'
    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER')
    if not next_url or not url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = resolve_url('home')

    return redirect(next_url)


@login_required
def edit_profile(request):
    profile, _ = Profile.objects.get_or_create(user=request.user)

    if request.method == 'POST':
        which = request.POST.get('which') or 'user'  # ← дефолт
        if which == 'user':
            form = ProfileEditForm(request.POST, request.FILES, instance=request.user)
            theme_form = ProfileThemeForm(prefix='theme', instance=profile)
            if form.is_valid():
                form.save()
                return redirect('edit_profile')
        elif which == 'theme':
            form = ProfileEditForm(instance=request.user)
            theme_form = ProfileThemeForm(request.POST, prefix='theme', instance=profile)
            if theme_form.is_valid():
                theme_form.save()
                return redirect('edit_profile')
    else:
        form = ProfileEditForm(instance=request.user)
        theme_form = ProfileThemeForm(prefix='theme', instance=profile)

    popularity = profile.popularity
    active_plan = get_active_vip_plan(request.user)
    vip_boost = active_plan.popularity_boost if active_plan else 0

    text_products = (
        TextProduct.objects
        .filter(owner=request.user, status='approved', is_active=True)
        .annotate(
            sales_count=Count('orders', filter=Q(orders__is_active=True)),
            avg_rating=Avg('ratings__rating'),           
            ratings_count=Count('ratings'),
        )
    )
    artworks = (
        Artwork.objects
        .filter(owner=request.user, status='approved', is_active=True)
        .filter(Q(available_copies__gt=0) | Q(available_copies__isnull=True))
        .annotate(
            sales_count=Count(
                'orders',
                filter=Q(orders__status__in=[ArtworkOrder.Status.COMPLETED,
                                             ArtworkOrder.Status.RELEASED])
            )
        )
        .prefetch_related('pages')  
        .order_by('-updated', '-created_at')
    )

    # Пока пустой список достижений
    achievements = []

    me = (request.user.__class__.objects
        .filter(pk=request.user.pk)
        .annotate(
            # учитываем только активные (успешные) текстовые заказы
            text_sales=Coalesce(
                Count('text_orders',
                    filter=Q(text_orders__is_active=True),
                    distinct=True), 0
            ),
            # учитываем только завершённые / релизнутые арт-заказы
            art_sales=Coalesce(
                Count('artwork_orders',
                    filter=Q(artwork_orders__status__in=[
                        ArtworkOrder.Status.COMPLETED,
                        ArtworkOrder.Status.RELEASED
                    ]),
                    distinct=True), 0
            ),
        )
        .annotate(total_sales=F('text_sales') + F('art_sales'))
        .values('total_sales', 'text_sales', 'art_sales')
        .first() or {'total_sales': 0, 'text_sales': 0, 'art_sales': 0})


    fixed_colors = [
        "#4086f7", "#f05522", "#f0224b", "#752938",
        "#29752e", "#62bd68", "#8abfbb", "#bfb48a",
        "#cc0e11", "#1d172b", "#806a76", "#cadbc8"
    ]

    vip_level = (active_plan.level if active_plan else None)

    return render(request, 'profile.html', {
        'form': form,
        'theme_form': theme_form,
        'profile': profile,
        'popularity_base': popularity,
        'vip_boost': vip_boost,
        'text_products': text_products,
        'artworks': artworks,
        'achievements': achievements,
        'fixed_colors': fixed_colors,
        'total_sales': me['total_sales'],
        'sales_breakdown': me,
        'active_plan': active_plan, 
        'vip_level': vip_level,   
        'gifts': [],    
        'lessons': [],
    })


@login_required
@require_POST
@csrf_protect
def upload_avatar(request):
    f = request.FILES.get('avatar')
    if not f:
        return JsonResponse({'ok': False, 'error': 'Файл не передан'}, status=400)

    if f.size > 5 * 1024 * 1024:  # 5 MB
        return JsonResponse({'ok': False, 'error': 'Файл больше 5 МБ'}, status=400)

    # Белый список расширений и MIME
    allowed_exts = {'.png', '.jpg', '.jpeg', '.webp'}
    allowed_mimes = {'image/png', 'image/jpeg', 'image/webp'}

    ext = os.path.splitext(f.name)[1].lower()
    if ext not in allowed_exts:
        return JsonResponse({'ok': False, 'error': 'Недопустимое расширение файла'}, status=400)

    # Content-Type от клиента не всегда надёжен, но проверим
    content_type = (getattr(f, 'content_type', '') or '').lower()
    if content_type and content_type not in allowed_mimes:
        return JsonResponse({'ok': False, 'error': 'Недопустимый тип содержимого'}, status=400)

    # Читаем в память и валидируем реальный формат через Pillow
    try:
        raw = f.read()
        img = Image.open(BytesIO(raw))
        real_fmt = (img.format or '').upper()  # 'PNG'/'JPEG'/'WEBP'/...
        if real_fmt not in {'PNG', 'JPEG', 'WEBP'}:
            return JsonResponse({'ok': False, 'error': 'Недопустимый формат изображения'}, status=400)
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Не удалось прочитать изображение'}, status=400)

    # Конвертируем к RGBA, чтобы единообразно сохранять PNG
    try:
        img = img.convert('RGBA')
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Проблема при обработке изображения'}, status=400)

    # Квадратная обрезка по центру
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top  = (h - side) // 2
    img = img.crop((left, top, left + side, top + side))

    # Анти-scan превью: жёсткий лимит 512×512
    img = img.resize((512, 512), Image.Resampling.LANCZOS)

    # Сохраняем как PNG (безопаснее), имя — UUID в директорию пользователя
    out = BytesIO()
    img.save(out, format='PNG', optimize=True)
    out.seek(0)

    filename = f"avatars/{request.user.pk}/{uuid.uuid4().hex}.png"
    request.user.profile_image.save(filename, ContentFile(out.read()), save=True)

    # Возвращаем актуальный URL (у тебя уже есть profile_image_url; если его нет — используй request.user.profile_image.url)
    return JsonResponse({'ok': True, 'url': getattr(request.user, 'profile_image_url', request.user.profile_image.url)})


@login_required
def user_profile_view(request, username):
    visited_user = get_object_or_404(request.user.__class__, username=username)

    if request.user.is_authenticated and request.user.username == username:
        return redirect('edit_profile')

    register_profile_visit(request, visited_user)

    profile, _ = Profile.objects.get_or_create(user=visited_user)
    popularity = profile.popularity
    active_plan = get_active_vip_plan(visited_user)
    vip_boost = active_plan.popularity_boost if active_plan else 0

    text_products = (
        TextProduct.objects
        .filter(owner=visited_user, status='approved', is_active=True)
        .annotate(
            total_sales=Count('orders', filter=Q(orders__is_active=True)),
            avg_rating=Avg('ratings__rating'),          
            ratings_count=Count('ratings'),   
        )
    )

    artworks = (
        Artwork.objects
        .filter(owner=visited_user, status='approved', is_active=True)
        .filter(Q(available_copies__gt=0) | Q(available_copies__isnull=True))
        .annotate(
            sales_count=Count(
                'orders',
                filter=Q(orders__status__in=[
                    ArtworkOrder.Status.COMPLETED,
                    ArtworkOrder.Status.RELEASED
                ])
            )
        )
        .prefetch_related('pages')
        .order_by('-updated', '-created_at')
    )


    agg = (visited_user.__class__.objects
            .filter(pk=request.user.pk)
            .annotate(
                # учитываем только активные (успешные) текстовые заказы
                text_sales=Coalesce(
                    Count('text_orders',
                        filter=Q(text_orders__is_active=True),
                        distinct=True), 0
                ),
                # учитываем только завершённые / релизнутые арт-заказы
                art_sales=Coalesce(
                    Count('artwork_orders',
                        filter=Q(artwork_orders__status__in=[
                            ArtworkOrder.Status.COMPLETED,
                            ArtworkOrder.Status.RELEASED
                        ]),
                        distinct=True), 0
                ),
            )
            .annotate(total_sales=F('text_sales') + F('art_sales'))
            .values('total_sales', 'text_sales', 'art_sales')
            .first() or {'total_sales': 0, 'text_sales': 0, 'art_sales': 0})

    achievements = []

    return render(request, 'user_profile.html', {
        'visited_user': visited_user,
        'profile': profile,
        'popularity_base': popularity,
        'vip_boost': vip_boost,
        'text_products': text_products,
        'artworks': artworks,
        'achievements': achievements,
        'total_sales': agg['total_sales'],
        'sales_breakdown': agg,
    })


def _bump_popularity_for_sale(seller, amount: Decimal):
    """
    +5 очков за продажу + 10% от стоимости (округление вниз до целого).
    """
    if seller is None or amount is None:
        return
    base = 5
    extra = int((Decimal(amount) * Decimal('0.10')).to_integral_value(rounding=ROUND_DOWN))
    inc = base + max(extra, 0)
    profile, _ = Profile.objects.get_or_create(user=seller)
    profile.popularity = (profile.popularity or 0) + inc
    profile.save(update_fields=['popularity'])



def register_profile_visit(request, visited_user):
    # 0) не считаем собственные визиты
    if not visited_user or (request.user.is_authenticated and request.user == visited_user):
        return

    # 1) залогиненные: 1 раз на пользователя (атомарно)
    if request.user.is_authenticated:
        obj, created = Visit.objects.get_or_create(
            visited=visited_user,
            visitor=request.user,
            defaults={
                "ip_address": (request.META.get("REMOTE_ADDR") or "")[:64],
                # если в Visit нет этого поля — убери строку ниже
                # "user_agent": (request.META.get("HTTP_USER_AGENT") or "")[:256],
            },
        )
        if created:
            profile, _ = Profile.objects.get_or_create(user=visited_user)
            profile.popularity = (profile.popularity or 0) + 1
            profile.save(update_fields=["popularity"])
        return  # <— важно! чтобы не упасть в блок анонимов

    # 2) анонимные: 1 раз на сессию (при желании можно сделать TTL)
    key = f"visited_profile_{visited_user.pk}"
    if not request.session.get(key):
        profile, _ = Profile.objects.get_or_create(user=visited_user)
        profile.popularity = (profile.popularity or 0) + 1
        profile.save(update_fields=["popularity"])
        request.session[key] = True


@csrf_protect
@login_required
def save_background_color(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Неверный метод запроса'})

    data = json.loads(request.body or "{}")
    color = (data.get('color') or "").strip()

    if not color:
        return JsonResponse({'status': 'error', 'message': 'Цвет не указан'})

    # 1) VIP-предустановки
    vip_tokens = {'vip-gold', 'vip-star', 'vip-galaxy'}
    if color in vip_tokens:
        plan = get_active_vip_plan(request.user)  # у вас уже используется в edit_profile
        if not plan:
            return JsonResponse({'status': 'error', 'message': 'Требуется VIP'}, status=403)
        lvl = (plan.level or "").upper()
        allowed = {'vip-gold'}  # GOLD
        if lvl == 'STAR':
            allowed |= {'vip-star'}
        if lvl == 'GALAXY':
            allowed |= {'vip-star', 'vip-galaxy'}
        if color not in allowed:
            return JsonResponse({'status': 'error', 'message': 'Недостаточный уровень VIP'}, status=403)

    else:
        # 2) Обычный HEX-цвет
        import re
        if not re.fullmatch(r'#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})', color):
            return JsonResponse({'status': 'error', 'message': 'Некорректный HEX'}, status=400)

    profile = getattr(request.user, 'profile', None)
    if not profile:
        return JsonResponse({'status': 'error', 'message': 'Профиль не найден'}, status=404)

    profile.background_color = color
    profile.save(update_fields=['background_color'])
    return JsonResponse({'status': 'success', 'value': color})


@login_required
@require_POST
def start_connect_onchain_wallet(request):
    """
    1) Генерация nonce для подписи в MetaMask
    2) Отправка 6-значного кода на email (2FA)
    """
    currency = request.POST.get("currency", "USDT")
    chain_id = int(getattr(settings, "CHAIN_ID", 56))

    # 2FA: создаём EmailVerification с 30-мин TTL (у тебя уже есть такая модель/логика)
    code = ''.join(random.choices(string.digits, k=6))
    expires = timezone.now() + timedelta(minutes=30)
    EmailVerification.objects.create(user=request.user, code=code, expires_at=expires)

    EmailMessage(
        subject="Подтверждение привязки кошелька (2FA)",
        body=f"Код подтверждения: {code}\nДействует до {expires.strftime('%H:%M %d.%m.%Y')}",
        to=[request.user.email]
    ).send()

    # nonce для подписи
    nonce = get_random_string(24)
    # создаём/обновляем запись-черновик кошелька для верификации
    w, _ = CryptoWallet.objects.get_or_create(
        user=request.user, currency=currency,
        defaults=dict(address="", is_external=True, is_primary=False, chain_id=chain_id)
    )
    w.verify_nonce = nonce
    w.save(update_fields=["verify_nonce"])

    return JsonResponse({"ok": True, "nonce": nonce, "chainId": chain_id})


@login_required
@require_POST
def verify_connect_onchain_wallet(request):
    address  = (request.POST.get("address") or "").strip()
    sig      = (request.POST.get("signature") or "").strip()
    code     = (request.POST.get("code") or "").strip()
    currency = (request.POST.get("currency") or "USDT").strip()
    client_chain = int(request.POST.get("chainId") or 0)

    if not ETH_ADDRESS_RE.fullmatch(address):
        return JsonResponse({"ok": False, "error": "Неверный формат адреса"}, status=400)
    if not SIG_RE.fullmatch(sig):
        return JsonResponse({"ok": False, "error": "Неверный формат подписи"}, status=400)
    if client_chain != getattr(settings, "CHAIN_ID", 0):
        return JsonResponse({"ok": False, "error": "Неверная сеть"}, status=400)

    ev = EmailVerification.objects.filter(
        user=request.user, code=code, expires_at__gte=timezone.now()
    ).first()
    if not ev:
        return JsonResponse({"ok": False, "error": "Неверный или просроченный код"}, status=400)

    try:
        # важный момент: по твоей модели есть unique_together (user, currency),
        # поэтому тут мы обновляем существующую запись (черновик) для этой валюты
        w = CryptoWallet.objects.get(user=request.user, currency=currency, is_external=True)
    except CryptoWallet.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Нет черновика кошелька"}, status=400)

    if not w.verify_nonce:
        return JsonResponse({"ok": False, "error": "Nonce отсутствует"}, status=400)

    msg = encode_defunct(text=f"Ospyral wallet link\nnonce={w.verify_nonce}")
    try:
        recovered = w3.eth.account.recover_message(msg, signature=sig)
    except Exception:
        return JsonResponse({"ok": False, "error": "Невалидная подпись"}, status=400)

    if recovered.lower() != address.lower():
        return JsonResponse({"ok": False, "error": "Подпись не соответствует адресу"}, status=400)

    with transaction.atomic():
        CryptoWallet.objects.filter(
            user=request.user, currency=currency, is_external=True
        ).update(is_primary=False)

        w.address = address
        w.is_primary = True
        w.verified_at = timezone.now()
        w.verify_nonce = ""  # одноразовый nonce
        w.save(update_fields=["address", "is_primary", "verified_at", "verify_nonce"])

        ev.delete()

    return JsonResponse({"ok": True, "address": address})


def wallets_view(request):

    tab = request.GET.get("tab", "balance")

    # Текущие кошельки + история депозитов (как было)
    wallets = request.user.crypto_wallets.prefetch_related(
        Prefetch(
            'txs',
            queryset=CryptoTransaction.objects.filter(tx_type='deposit').order_by('-created_at'),
            to_attr='deposit_txs'
        )
    )

    # Донатные пресеты (мягкое пополнение OSP оффчейн)
    deposit_presets = [5, 10, 25, 50, 100, 250, 500, 1000]

    # История (журнал) – последние записи, пагинация простая через GET ?page=
    ledger_qs = LedgerEntry.objects.filter(user=request.user).order_by("-created_at")
    page_obj = Paginator(ledger_qs, 20).get_page(request.GET.get("page", 1))

    # Покупки/Продажи для вкладок (короткие списки здесь; при желании вынесем в отдельные страницы)
    text_purchases = TextProductOrder.objects.filter(user=request.user, is_active=True).select_related("product", "product__owner")
    art_purchases  = ArtworkOrder.objects.filter(user=request.user, status__in=[ArtworkOrder.Status.COMPLETED, ArtworkOrder.Status.RELEASED]).select_related("artwork", "artwork__owner")
    text_sales = TextProductOrder.objects.filter(product__owner=request.user, is_active=True).select_related("user", "product")
    art_sales  = ArtworkOrder.objects.filter(artwork__owner=request.user, status__in=[ArtworkOrder.Status.COMPLETED, ArtworkOrder.Status.RELEASED]).select_related("user", "artwork")

    unread_notifications = request.user.notifications.filter(is_read=False)

    return render(request, 'wallets.html', {
        'tab': tab,
        'wallets': wallets,
        'deposit_presets': deposit_presets,
        'page_obj': page_obj,  # журнал
        'text_purchases': text_purchases,
        'art_purchases': art_purchases,
        'text_sales': text_sales,
        'art_sales': art_sales,
        'unread_notifications': unread_notifications,
    })

def _to_base_units(amount_dec: Decimal, decimals: int) -> int:
    q = Decimal(10) ** decimals
    return int((amount_dec * q).to_integral_value(rounding=ROUND_DOWN))

def check_user_token_balance_or_403(user, token_addr: str, required_amount_dec: Decimal):
    """
    Проверка, что primary-кошелёк пользователя для USDT имеет баланс >= требуемой суммы.
    """
    w = (CryptoWallet.objects
         .filter(user=user, currency=Currency.USDT, is_external=True, is_primary=True)
         .first())
    if not w or not w.address:
        return HttpResponseForbidden("Подключите USDT-кошелёк")

    token = w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=(getattr(settings, "USDT_ABI", None) or getattr(settings, "ERC20_ABI", None) or []))
    base_needed = _to_base_units(required_amount_dec, int(getattr(settings, "USDT_DECIMALS", 6)))
    try:
        bal = token.functions.balanceOf(Web3.to_checksum_address(w.address)).call()
    except Exception:
        return HttpResponseForbidden("Не удалось получить баланс токена")

    if int(bal) < int(base_needed):
        return HttpResponseForbidden("Недостаточно средств на USDT-кошельке")

    return w 

@login_required
def my_purchases_view(request):
    """
    Мои покупки: текстовые и арт. Для текста считаем is_active=True, для арта — COMPLETED/RELEASED.
    """
    text_qs = TextProductOrder.objects.filter(user=request.user, is_active=True).select_related("product", "product__owner")
    art_qs  = ArtworkOrder.objects.filter(user=request.user, status__in=[ArtworkOrder.Status.COMPLETED, ArtworkOrder.Status.RELEASED]).select_related("artwork", "artwork__owner")
    purchases = list(text_qs) + list(art_qs)

    paginator = Paginator(purchases, 20)
    page_obj = paginator.get_page(request.GET.get("page", 1))
    return render(request, "purchases.html", {"page_obj": page_obj})

@login_required
def my_sales_view(request):
    """
    Мои продажи: заказы на мои товары. Для текста — активные, для арта — COMPLETED/RELEASED.
    """
    text_qs = TextProductOrder.objects.filter(product__owner=request.user, is_active=True).select_related("user", "product")
    art_qs  = ArtworkOrder.objects.filter(artwork__owner=request.user, status__in=[ArtworkOrder.Status.COMPLETED, ArtworkOrder.Status.RELEASED]).select_related("user", "artwork")
    sales = list(text_qs) + list(art_qs)

    paginator = Paginator(sales, 20)
    page_obj = paginator.get_page(request.GET.get("page", 1))
    return render(request, "sales.html", {"page_obj": page_obj})



def catalog_text_products(request):
    user = request.user if request.user.is_authenticated else None

    # === Параметры из формы ===
    q        = (request.GET.get('q') or '').strip()
    cat      = (request.GET.get('cat') or '').strip()
    ccy      = (request.GET.get('ccy') or '').strip()
    nsfw     = request.GET.get('nsfw', '')  # "", "1", "only"
    pmin_raw = (request.GET.get('price_min') or '').strip()
    pmax_raw = (request.GET.get('price_max') or '').strip()
    sort_ui  = (request.GET.get('sort') or 'new').strip()  # "new" | "price_asc" | "price_desc"
    page_raw = (request.GET.get('page') or '1').strip()

    # Страница -> int
    try:
        page = max(int(page_raw), 1)
    except ValueError:
        page = 1

    # === Базовый QS ===
    qs = (
        TextProduct.objects
        .filter(status=TextProduct.Status.APPROVED, is_active=True, is_deleted=False)
        .select_related('owner__profile')
        .annotate(
            avg_rating=Avg('ratings__rating'),
            ratings_count=Count('ratings')
        )
    )

    # Поиск (название/теги; можно расширить до description)
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(keywords__icontains=q))

    # Категория (точное совпадение по value)
    if cat:
        qs = qs.filter(category=cat)

    # Валюта (точное совпадение по value)
    if ccy:
        qs = qs.filter(currency=ccy)

    # NSFW режим
    # "": по умолчанию скрываем NSFW; "1": показываем всё; "only": только NSFW
    if nsfw == "":
        qs = qs.filter(nsfw=False)
    elif nsfw == "only":
        qs = qs.filter(nsfw=True)
    # nsfw == "1" -> без фильтра

    # Диапазон цены
    def as_decimal(x):
        try:
            from decimal import Decimal
            d = Decimal(str(x))
            return d if d >= 0 else None
        except Exception:
            return None

    pmin = as_decimal(pmin_raw)
    pmax = as_decimal(pmax_raw)
    if pmin is not None:
        qs = qs.filter(price__gte=pmin)
    if pmax is not None:
        qs = qs.filter(price__lte=pmax)

    # Сортировка UI -> реальные поля
    # "new" -> created_at DESC; "price_asc"/"price_desc" -> price ASC/DESC
    ORDER_MAP = {
        'new':        ('created_at', True),   # True = desc
        'price_asc':  ('price', False),
        'price_desc': ('price', True),
    }
    field, desc = ORDER_MAP.get(sort_ui, ('created_at', True))

    if field == 'avg_rating':
        qs = qs.order_by(
            OrderBy(F('avg_rating'), descending=desc, nulls_last=True),
            OrderBy(F('created_at'), descending=True),
        )
    else:
        qs = qs.order_by(
            OrderBy(F(field), descending=desc),
            OrderBy(F('created_at'), descending=True),
        )

    # Пагинация
    paginator = Paginator(qs, 24)
    page_obj  = paginator.get_page(page)

    # Списки для селектов (value, label) из модели
    # Категории: плоский список из вложенных choices
    raw_cats = TextProduct._meta.get_field('category').choices  # вложенные группы
    categories = []
    for item in raw_cats:
        # item может быть ('value','label') или ('Group', (('v','l'),...))
        if isinstance(item[1], (list, tuple)):
            for v, label in item[1]:
                categories.append((v, label))
        else:
            v, label = item
            categories.append((v, label))

    currencies = list(TextProduct._meta.get_field('currency').choices)

    context = {
        # значения для формы
        'q': q, 'cat': cat, 'ccy': ccy, 'nsfw': nsfw,
        'pmin': pmin_raw, 'pmax': pmax_raw,
        'sort': sort_ui,
        'categories': categories,
        'currencies': currencies,

        # данные листинга/пагинации
        'page': page_obj,                 # <— для твоего info-блока «Стр. X из Y»
        'page_obj': page_obj,
        'products': page_obj.object_list,
    }
    return render(request, 'catalog_text_products.html', context)


@login_required
def add_text_product(request):

    blocked, until = check_creation_cooldown(request.user)

    if blocked and request.method == 'GET':
        messages.info(request, "Создание продукта временно недоступно до окончания ограничения.")
        return redirect('create_product_hub')
    
    if request.method == 'POST':
        if blocked:
            remaining = int((until - timezone.now()).total_seconds())
            messages.error(request, "Лимит создания исчерпан. Дождитесь окончания ограничения.")
            # Вернём форму с таймером
            form = TextProductForm(request.POST)
            return render(request, 'portfolio.html', {
                'form': form, 'blocked': True, 'remaining_seconds': max(0, remaining), 'until': until
            }, status=403)
        
        if COOLDOWN_ENABLED and blocked:
            remaining_seconds = int((until - timezone.now()).total_seconds())
            messages.error(request, "Создание продукта временно недоступно. Подождите окончания кулдауна.")
            form = TextProductForm(request.POST)
            return render(request, 'add_text_product.html', {'form': form, 'blocked': True, 'until': until, 'remaining_seconds': remaining_seconds}, status=403)
        
        draft_count = TextProduct.objects.filter(
            owner=request.user,
            status=TextProduct.Status.DRAFT
        ).count()

        form = TextProductForm(request.POST)

        if 'save_later' in request.POST:
            if draft_count >= 5:
                messages.error(request, "У вас уже 5 черновиков — удалите старый, чтобы создать новый.")
                return redirect('portfolio')
            if form.is_valid():
                prod = form.save(owner=request.user)

                CreationQuotaLog.objects.create(
                    user=request.user,
                    reason=CreationQuotaLog.Reason.TEXT_CREATED
                )

                UserProductCooldown.trigger(request.user, reason='text_created')
                messages.success(request, "Черновик сохранён — вы можете закончить его позже.")
                return HttpResponseRedirect(reverse('portfolio') + '#drafts')
            return render(request, 'add_text_product.html', {'form': form}, status=400)

        if 'submit_review' in request.POST:
            if form.is_valid():
                prod = form.save(owner=request.user)
                prod.submit_for_review()

                CreationQuotaLog.objects.create(
                    user=request.user,
                    reason=CreationQuotaLog.Reason.TEXT_CREATED
                )

                UserProductCooldown.trigger(request.user, reason='text_submitted')
                messages.success(request, "Продукт отправлен на проверку.")
                return HttpResponseRedirect(reverse('portfolio') + '#pending')
            return render(request, 'add_text_product.html', {'form': form}, status=400)

        # Нажали что-то иное / отсутствует имя кнопки — просто показать ошибки
        return render(request, 'add_text_product.html', {'form': form}, status=400)

    # GET
    form = TextProductForm()
    return render(request, 'add_text_product.html', {'form': form, 'blocked': blocked, 'until': until, 'remaining_seconds': (int((until - timezone.now()).total_seconds()) if (blocked and until) else 0)})


@login_required
def owner_text_product(request, pk: int):
    """
    Панель владельца текста: полный контент + быстрые действия и метрики.
    """
    product = get_object_or_404(TextProduct, pk=pk, owner=request.user)

    # Полный контент владельцу всегда доступен
    content = product.decrypt_content()

    # Метрики и состояние
    sales_count = product.orders.count()
    avg_rating = product.ratings.aggregate(avg=Avg('rating'))['avg']  # у нас поле rating
    can_edit    = product.status in [TextProduct.Status.DRAFT, TextProduct.Status.REJECTED]
    can_submit  = product.status == TextProduct.Status.DRAFT
    can_stop    = product.status == TextProduct.Status.APPROVED and product.is_active
    can_resume  = product.status == TextProduct.Status.APPROVED and not product.is_active
    can_delete  = True  # мягкое удаление всегда допустимо для владельца

    # Быстрые URL-ы действий
    urls = {
        'edit':          reverse('edit_text_product', kwargs={'pk': product.pk}),
        'submit':        reverse('submit_text_product', kwargs={'pk': product.pk}),
        'stop_sale':     reverse('stop_sale_text_product', kwargs={'pk': product.pk}),
        'resume_sale':   reverse('resume_sale_text_product', kwargs={'pk': product.pk}),
        'delete':        reverse('delete_text_product', kwargs={'pk': product.pk}),
        'ack_reject':    reverse('acknowledge_rejection', kwargs={'pk': product.pk}),
        'public_preview':reverse('public_view_text_product', kwargs={'product_id': product.pk}),
    }

    context = {
        'product': product,
        'content': content,
        'sales_count': sales_count,
        'avg_rating': avg_rating,
        'can_edit': can_edit,
        'can_submit': can_submit,
        'can_stop': can_stop,
        'can_resume': can_resume,
        'can_delete': can_delete,
        'urls': urls,
        # полезно показать админ-оценки/блокировки и причину отклонения
        'quality': product.quality,
        'uniqueness': product.uniqueness,
        'spelling': product.spelling,
        'blocked_until': product.blocked_until,
        'block_reason': product.block_reason,
        'rejection_reason': product.rejection_reason,
    }
    return render(request, 'text_products/owner_panel.html', context)


@login_required
def view_text_product(request, product_id):
    product = get_object_or_404(TextProduct, id=product_id)

    if product.owner_id == request.user.id:
        return redirect('owner_text_product', pk=product.id)

    is_owner = request.user == product.owner
    is_moderator = request.user.is_staff
    has_purchased = TextProductOrder.objects.filter(
        user=request.user, product=product, is_active=True
    ).exists()

    can_view_content = is_owner or is_moderator or has_purchased
    
    avg_rating = TextProductRating.objects.filter(product=product)\
        .aggregate(avg=Avg('rating'))['avg']
    
    user_rating = TextProductRating.objects.filter(product=product, user=request.user)\
        .values_list('rating', flat=True).first()
    
    decrypted_content = product.decrypt_content() if can_view_content else None
    keywords = [tag.strip() for tag in product.keywords.split(',')] if product.keywords else []

    context = {
        'product': product,
        'can_view_content': can_view_content,
        'decrypted_content': decrypted_content,
        'keywords': keywords,
        'user_rating': user_rating,
        'avg_rating': avg_rating,
    }
    return render(request, 'view_text_product.html', context)

@login_required
def download_text(request, product_id):
    from .models import TextProduct
    tp = get_object_or_404(TextProduct, pk=product_id)

    is_owner = (tp.owner_id == request.user.id)
    is_staff = getattr(request.user, "is_staff", False)

    has_purchase = False
    try:
        from .models import TextProductOrder
        has_purchase = TextProductOrder.objects.filter(
            user=request.user, product=tp, is_active=True
        ).exists()
    except Exception:
        pass

    if not (is_owner or is_staff or has_purchase):
        return HttpResponseForbidden("Недоступно для скачивания")

    content = tp.decrypt_content() or ""
    buf = BytesIO(content.encode("utf-8"))
    fname = f"{tp.title}.txt"
    return FileResponse(buf, as_attachment=True, filename=fname)


@login_required
def read_text_product(request, product_id):
    from .models import TextProduct
    tp = get_object_or_404(TextProduct, pk=product_id)

    is_owner = (tp.owner_id == request.user.id)
    is_staff = getattr(request.user, "is_staff", False)

    has_purchase = False
    try:
        from .models import TextProductOrder
        has_purchase = TextProductOrder.objects.filter(
            user=request.user, product=tp, is_active=True
        ).exists()
    except Exception:
        pass

    can_view = is_owner or is_staff or has_purchase
    if not can_view:
        # замени на имя твоей публичной вью, если другое:
        return redirect("public_view_text_product", product_id=tp.id)

    content = tp.decrypt_content() or ""
    pages = content.split(SEPARATOR) if SEPARATOR in content else split_into_pages(content, target_chars=1800)

    # стартовая страница из query (?p=)
    try:
        start = int(request.GET.get("p", "1"))
    except ValueError:
        start = 1
    start = max(1, min(start, len(pages))) if pages else 1

    return render(request, "text_reader.html", {
        "product": tp,
        "pages": pages,
        "start_page": start
    })


def split_into_pages(text: str, target_chars: int = 1800):
    parts = []
    buf = []
    count = 0
    for para in text.split("\n\n"):
        if count + len(para) + 2 > target_chars and buf:
            parts.append("\n\n".join(buf).strip())
            buf, count = [], 0
        buf.append(para)
        count += len(para) + 2
    if buf:
        parts.append("\n\n".join(buf).strip())
    # пустые уберём
    return [p for p in parts if p]

@login_required
def public_view_text_product(request, product_id):
    product = get_object_or_404(TextProduct, pk=product_id)

    # владелец — в приватное вью
    if product.owner_id == request.user.id:
        return redirect('owner_text_product', pk=product.id)

    # --- тянем последний активный заказ и escrow ---
    has_purchased = False
    order = (
        TextProductOrder.objects
        .filter(user=request.user, product=product, is_active=True)
        .order_by('-id')
        .first()
    )

    escrow = None
    if order:
        has_purchased = True
        ct = ContentType.objects.get_for_model(TextProductOrder)
        escrow = (
            Escrow.objects
            .filter(order_ct=ct, order_id=order.id)
            .order_by('-id')
            .first()
        )

    can_view_content = request.user.is_staff or has_purchased
    decrypted_content = product.decrypt_content() if can_view_content else None

    # секунды до авто-релиза для таймера (если есть escrow и он с таймером)
    seconds_to_autorelease = escrow.seconds_to_autorelease if escrow else None

    avg_rating = product.ratings.aggregate(avg=Avg('rating'))['avg']  # rating, не score
    user_rating = TextProductRating.objects.filter(
        product=product, user=request.user
    ).values_list('rating', flat=True).first()
    
    keywords = [k.strip() for k in (product.keywords or '').split(',') if k.strip()]

    return render(request, 'view_text_product.html', {
        'product': product,
        'can_view_content': can_view_content,
        'decrypted_content': decrypted_content,
        'avg_rating': avg_rating,
        'keywords': keywords,
        'user_rating': user_rating,
        'has_purchased': has_purchased,
        'order': order,
        'escrow': escrow,
        'seconds_to_autorelease': seconds_to_autorelease,
        'CHAIN_ID': settings.CHAIN_ID,
        'ESCROW_ADDRESS': settings.ESCROW_CONTRACT_ADDRESS,
    })

@login_required
def submit_text_product(request, pk):
    prod = get_object_or_404(TextProduct, pk=pk, owner=request.user)
    if prod.status != TextProduct.Status.DRAFT:
        messages.warning(request, "Нельзя отправить на проверку этот продукт.")
    else:
        prod.submit_for_review()
        messages.success(request, "Продукт отправлен на проверку.")
    return redirect('portfolio')



@login_required
@require_POST
def delete_text_product(request, pk):
    prod = get_object_or_404(TextProduct, pk=pk, owner=request.user)

    prod.is_active = False
    prod.is_deleted = True
    prod.save(update_fields=['is_active', 'is_deleted'])

    messages.success(request, f"«{prod.title}» был удален.")
    return redirect(reverse('portfolio') + '?tab=drafts')


@login_required
@require_POST
@transaction.atomic
def buy_text_product(request, product_id):
    # 1) Находим продукт и проверяем статус
    product = get_object_or_404(
        TextProduct,
        pk=product_id,
        status=TextProduct.Status.APPROVED,
        is_active=True,
        is_deleted=False,
    )

    # 2) Блокируем покупку собственного товара (юзер-френдли)
    if product.owner_id == request.user.id:
        messages.info(request, "Это ваш продукт — покупать не нужно.")
        return redirect('public_view_text_product', product_id=product.id)

    # 3) Защита от повторной покупки (юзер-френдли; в любом случае внутри есть доп.проверка)
    if TextProductOrder.objects.filter(user=request.user, product=product, is_active=True).exists():
        messages.info(request, "Вы уже купили этот продукт.")
        return redirect('public_view_text_product', product_id=product.id)

    # 4) Сейчас поддерживаем только OSP (USDT — позже/ончейн)
    if product.currency != Currency.OSP:
        messages.warning(request, "Покупка этого типа за USDT будет доступна позже. Сейчас доступна покупка за OSP.")
        return redirect('public_view_text_product', product_id=product.id)

    # 5) Единый путь — списание, логирование и ESCROW делает process_purchase(...)
    result = process_purchase(request.user, TextProduct, product_id, currency=product.currency)

    # process_purchase может вернуть HttpResponse (например, 403 — недостаточно средств)
    if isinstance(result, HttpResponse):
        try:
            # аккуратно покажем текст причины (если есть)
            reason = result.content.decode('utf-8').strip()
        except Exception:
            reason = ""
        if result.status_code == 403 and reason:
            messages.error(request, reason)
        elif result.status_code == 403:
            messages.error(request, "Недостаточно средств или нет доступа к покупке.")
        else:
            messages.error(request, "Не удалось оформить покупку.")
        return redirect('public_view_text_product', product_id=product.id)

    # Если None — заказ уже существовал (доп.страховка от гонок)
    if result is None:
        messages.info(request, "Вы уже купили этот продукт.")
        return redirect('public_view_text_product', product_id=product.id)

    # Успех: order создан, деньги списаны, escrow удержан
    messages.success(request, "Покупка оформлена: средства зарезервированы в эскроу.")
    return redirect('public_view_text_product', product_id=product.id)



@login_required
@require_POST
def confirm_text_order(request, order_id: int):
    order = get_object_or_404(TextProductOrder, pk=order_id, user=request.user)
    ct = ContentType.objects.get_for_model(TextProductOrder)
    esc = get_object_or_404(Escrow, order_ct=ct, order_id=order.id)

    if esc.status != Escrow.Status.HELD:
        messages.info(request, "Заказ уже обработан.")
        return redirect("public_view_text_product", product_id=order.product_id)


    if order.product.currency == Currency.OSP:
        with transaction.atomic():
            # 1) пометить escrow RELEASED
            esc.status = Escrow.Status.RELEASED
            esc.released_at = timezone.now()
            esc.save(update_fields=["status", "released_at"])

            # 2) начислить продавцу (с комиссией платформе)
            seller_wallet = CryptoWallet.objects.select_for_update().get(
                user=order.product.owner, currency=Currency.OSP
            )
            # аккуратная проводка: платформа -> fee, продавцу -> net
            settle_sale_with_platform_fee_osp(
                total_amount_micros=int(esc.amount_micros),
                seller_wallet=seller_wallet,
                order_ref=f"text:{order.id}"
            )

        messages.success(request, "Оплата подтверждена: средства выпущены продавцу.")
        return redirect("public_view_text_product", product_id=order.product_id)


    # Аналогично complete_order() для арта: просим сделать onchain release
    messages.info(request, "Чтобы завершить заказ, подтвердите транзакцию release() в кошельке. "
                           "После майнинга события статус обновится автоматически.")
    # Можно проставить легкий внутренний флаг/UI-состояние, если хочешь.
    return redirect("public_view_text_product", product_id=order.product_id)


@login_required
@require_POST
def dispute_text_order(request, order_id: int):
    return redirect(f"{reverse('refunds_hub')}?type=text&order={order_id}")

@login_required
@require_POST
def stop_sale_text_product(request, pk):
    prod = get_object_or_404(TextProduct, pk=pk, owner=request.user)
    if prod.status != TextProduct.Status.APPROVED:
        messages.error(request, "Можно приостанавливать только одобренные продукты.")
        return redirect(reverse('portfolio') + '?tab=on_sale')

    prod.is_active = False
    prod.save(update_fields=['is_active'])
    messages.success(request, f"«{prod.title}» снят с продажи.")
    return redirect(reverse('portfolio') + '?tab=on_sale')

@login_required
@require_POST
def resume_sale_text_product(request, pk):
    prod = get_object_or_404(TextProduct, pk=pk, owner=request.user)
    if prod.status != TextProduct.Status.APPROVED:
        messages.error(request, "Можно возобновлять продажу только одобренных продуктов.")
        return redirect(reverse('portfolio') + '?tab=on_sale')

    prod.is_active = True
    prod.save(update_fields=['is_active'])
    messages.success(request, f"«{prod.title}» снова в продаже.")
    return redirect(reverse('portfolio') + '?tab=on_sale')



@login_required
def acknowledge_rejection(request, pk):
    prod = get_object_or_404(TextProduct, pk=pk, owner=request.user, status=TextProduct.Status.REJECTED)
    # переводим обратно в черновик
    prod.status = TextProduct.Status.DRAFT
    prod.rejection_reason = ''
    prod.save(update_fields=['status','rejection_reason'])
    messages.success(request, "Причина отказа принята, продукт переведён в черновики.")
    return redirect('portfolio')


@login_required
def rate_text_product(request, product_id):
    product = get_object_or_404(TextProduct, id=product_id)

    # 1) Разрешаем ставить оценку только покупателю
    has_purchased = TextProductOrder.objects.filter(
        user=request.user, product=product, is_active=True
    ).exists()

    if not has_purchased:
        messages.error(request, "Оценку может оставить только покупатель.")
        return redirect('view_text_product', product_id=product.id)

    # 2) Запрет на изменение уже отправленной оценки
    if TextProductRating.objects.filter(product=product, user=request.user).exists():
        messages.info(request, "Вы уже оценили этот продукт, изменить оценку нельзя.")
        return redirect('view_text_product', product_id=product.id)

    if request.method == 'POST':
        try:
            rating_val = int(request.POST.get('rating'))
        except (ValueError, TypeError):
            messages.error(request, "Некорректная оценка.")
            return redirect('view_text_product', product_id=product.id)

        if 1 <= rating_val <= 5:
            TextProductRating.objects.create(product=product, user=request.user, rating=rating_val)
            messages.success(request, "Спасибо за вашу оценку!")
        else:
            messages.error(request, "Оценка должна быть от 1 до 5.")

        return redirect('view_text_product', product_id=product.id)

    return render(request, 'rate_text_product.html', {'product': product})



@login_required
def artwork_create(request):
    """
    1) Показывает форму создания артворка.
    2) При POST сохраняет артворк и редиректит на artwork_pages.
    """
    blocked, until = check_creation_cooldown(request.user)

    if blocked and request.method == 'GET':
        messages.info(request, "Создание продукта временно недоступно до окончания ограничения.")
        return redirect('create_product_hub')

    if request.method == 'POST':

        blocked, until, _ = check_creation_quota(request.user)
        if request.method == "POST":
            if blocked:
                remaining = int((until - timezone.now()).total_seconds())
                messages.error(request, "Лимит создания исчерпан. Дождитесь окончания ограничения.")
                # Вернём форму с таймером
                form = ArtworkForm(request.POST, request.FILES)
                return render(request, 'Шаблон.html', {
                    'form': form, 'blocked': True, 'remaining_seconds': max(0, remaining), 'until': until
                }, status=403)
            
        if COOLDOWN_ENABLED and blocked:
            remaining_seconds = int((until - timezone.now()).total_seconds())
            messages.error(request, "Создание продукта временно недоступно. Подождите окончания кулдауна.")
            form = ArtworkForm(request.POST, request.FILES)
            return render(request, 'artwork/artwork_create.html', {
                'form': form, 'blocked': True, 'until': until, 'remaining_seconds': remaining_seconds
            }, status=403)

        form = ArtworkForm(request.POST, request.FILES)
        if form.is_valid():
            art = form.save(commit=False)
            art.owner = request.user
            art.save()

            CreationQuotaLog.objects.create(
                user=request.user,
                reason=CreationQuotaLog.Reason.TEXT_CREATED
            )

            UserProductCooldown.trigger(request.user, reason='art_created')
            
            return redirect('artwork_pages', pk=art.pk)
    else:
        form = ArtworkForm()
    return render(request, 'artwork/artwork_create.html', {
        'form': form, 'blocked': blocked, 'until': until, 'remaining_seconds': (int((until - timezone.now()).total_seconds()) if (blocked and until) else 0)
    })

@login_required
def artwork_pages(request, pk):
    """
    Отображает Dropzone-зону и список миниатюр уже загруженных страниц.
    """
    artwork = get_object_or_404(Artwork, pk=pk, owner=request.user)
    existing_pages = artwork.pages.order_by('order').all()
    return render(request, 'artwork/artwork_pages.html', {
        'artwork':       artwork,
        'existing_pages': existing_pages,
        'has_pages':      existing_pages.exists(),
    })


@login_required
def create_artwork_page(request, pk):
    """
    AJAX-эндпоинт: POST с файлом 'image', создаём новую страницу (через форму).
    """
    if request.method != 'POST' or 'image' not in request.FILES:
        return HttpResponseBadRequest("Неверный запрос")

    artwork = get_object_or_404(Artwork, pk=pk, owner=request.user)
    if artwork.pages.count() >= 10:
        return JsonResponse({'success': False, 'errors': 'Достигнут лимит: не более 10 страниц.'}, status=400)

    next_order = artwork.pages.count()

    # Запускаем валидацию и нормализацию через форму (MIME/size + resize/compress)
    form = ArtworkImageForm(
        data={'order': next_order},
        files={'image': request.FILES['image']}
    )
    if not form.is_valid():
        # Вернём структуру ошибок формы
        return JsonResponse({'success': False, 'errors': form.errors.get_json_data()}, status=400)

    page = form.save(commit=False)
    page.artwork = artwork
    page.save()

    return JsonResponse({
        'success': True,
        'page_id': page.id,
        'url': page.image.url
    })


@login_required
def delete_artwork_page(request, page_id):
    """
    AJAX: удаляем страницу и пересчитываем order у оставшихся.
    """
    if request.method != 'POST':
        return HttpResponseBadRequest("Неверный метод")

    page = get_object_or_404(ArtworkImage, pk=page_id, artwork__owner=request.user)
    artwork = page.artwork
    page.delete()

    remaining = artwork.pages.order_by('order').all()
    for idx, p in enumerate(remaining):
        if p.order != idx:
            p.order = idx
            p.save()

    return JsonResponse({'success': True})


@login_required
def censor_artwork_page(request, page_id):
    """
    AJAX: сохраняем «отретушированное» изображение из dataURL (editedImage).
    """
    if request.method != 'POST':
        return HttpResponseBadRequest("Неверный метод")

    page = get_object_or_404(ArtworkImage, pk=page_id, artwork__owner=request.user)
    data_url = request.POST.get('editedImage')
    if not data_url:
        return JsonResponse({'success': False, 'errors': 'Нет данных изображения'})

    try:
        header, encoded = data_url.split(',', 1)
    except ValueError:
        return JsonResponse({'success': False, 'errors': 'Неправильный формат dataURL'})

    if 'image/png' in header:
        ext = 'png'
    elif 'image/jpeg' in header or 'image/jpg' in header:
        ext = 'jpg'
    else:
        ext = 'png'

    binary_data = base64.b64decode(encoded)
    file_name   = f"censored_{uuid.uuid4().hex[:10]}.{ext}"
    content_file = ContentFile(binary_data, name=file_name)

    if page.censored_image:
        page.censored_image.delete(save=False)
    page.censored_image = content_file
    page.save()

    return JsonResponse({
        'success': True,
        'url':     page.censored_image.url
    })


@login_required
def artwork_detail(request, pk):
    """
    Финальный просмотр артворка: показываем данные + все страницы (оригинал/цензура).
    """
    artwork = get_object_or_404(Artwork, pk=pk, owner=request.user)
    pages   = artwork.pages.order_by('order').all()

    if not pages.exists():
        messages.error(request, "Добавьте хотя бы одну страницу, чтобы перейти к предпросмотру.")
        return redirect('artwork_pages', pk=artwork.pk)
    
    return render(request, 'artwork/artwork_detail.html', {
        'artwork': artwork,
        'pages':    pages,
    })

@login_required
def download_artwork_zip(request, artwork_id):
    from .models import Artwork
    art = get_object_or_404(Artwork, pk=artwork_id)

    is_owner = (art.owner_id == request.user.id)
    is_staff = getattr(request.user, "is_staff", False)

    has_purchase = False
    try:
        from .models import ArtworkOrder
        has_purchase = ArtworkOrder.objects.filter(
            user=request.user, artwork=art,
            status__in=['completed', 'released', 'CONFIRMED', 'RELEASED']
        ).exists()
    except Exception:
        pass

    if not (is_owner or is_staff or has_purchase):
        return HttpResponseForbidden("Недоступно для скачивания")

    buf = BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for p in art.pages.order_by('order'):
            # осторожно: .image.open() читает из Storage
            file = p.image
            file.open("rb")
            try:
                ext = (file.name.rsplit('.', 1)[-1] or 'jpg').lower()
                zf.writestr(f"{p.order:03d}.{ext}", file.read())
            finally:
                file.close()
    buf.seek(0)
    fname = f"{art.title}.zip"
    return FileResponse(buf, as_attachment=True, filename=fname)



@login_required
def artwork_viewer(request, artwork_id):

    art = get_object_or_404(Artwork, pk=artwork_id)

    is_owner = (art.owner_id == request.user.id)
    is_staff = getattr(request.user, "is_staff", False)

    # Покупка полностью завершена
    has_purchase_done = ArtworkOrder.objects.filter(
        user=request.user, artwork=art,
        status__in=[ArtworkOrder.Status.COMPLETED, ArtworkOrder.Status.RELEASED],
    ).exists()

    # Доступ при удержании средств в эскроу (HELD)
    access_by_escrow = False
    last_order = ArtworkOrder.objects.filter(user=request.user, artwork=art).order_by('-id').first()
    if last_order:
        ct = ContentType.objects.get_for_model(ArtworkOrder)
        escrow = Escrow.objects.filter(order_ct=ct, order_id=last_order.id).first()
        if escrow and escrow.status == Escrow.Status.HELD:
            access_by_escrow = True

    has_access = is_owner or is_staff or has_purchase_done or access_by_escrow

    pages = art.pages.order_by('order').all()
    images = []
    for p in pages:
        if has_access or not p.censored_image:
            url = p.image.url
        else:
            url = p.censored_image.url
        images.append({"url": url, "num": p.order})

    try:
        start = int(request.GET.get("page", "1"))
    except (TypeError, ValueError):
        start = 1
    start = max(1, min(start, len(images))) if images else 1

    return render(request, "artwork_viewer.html", {
        "artwork": art,
        "images": images,
        "start_page": start,
        "has_access": has_access,
    })



@login_required
@require_POST
def toggle_sale_artwork(request, pk):
    art = get_object_or_404(Artwork, pk=pk, owner=request.user)

    # Разрешаем менять только флаг продажи (мы уже правили clean/save под это)
    if art.status == Artwork.Status.APPROVED:
        # просто инвертируем
        art.is_active = not art.is_active
        art.save(update_fields=["is_active"])
    else:
        # можно оставить запрет/или разрешить — решай
        return HttpResponseBadRequest("Нельзя менять продажу для этого статуса")

    # Возвращаем кусок HTML кнопки (partial), который заменит себя на странице
    return render(request, "partials/_artwork_sale_button.html", {"artwork": art, "request": request})

@login_required
@require_POST
def delete_artwork(request, pk):
    # мягкое удаление — как у текстов (там is_active=False, is_deleted=True) :contentReference[oaicite:13]{index=13}
    art = get_object_or_404(Artwork, pk=pk, owner=request.user)
    art.is_active = False
    art.is_deleted = True
    art.save(update_fields=["is_active","is_deleted"])
    messages.success(request, f"«{art.title}» удалён из продажи.")
    return redirect('portfolio')

@login_required
@require_POST
def stop_sale_artwork(request, pk):
    art = get_object_or_404(Artwork, pk=pk, owner=request.user)
    if art.status != Artwork.Status.APPROVED:
        messages.error(request, "Приостановить можно только одобренный артворк.")
        return redirect(reverse('portfolio') + '?tab=on_sale')
    if art.is_active:
        art.is_active = False
        art.save(update_fields=["is_active"])
        messages.success(request, f"«{art.title}» приостановлен.")
    return redirect(reverse('portfolio') + '?tab=on_sale')

@login_required
@require_POST
def resume_sale_artwork(request, pk):
    art = get_object_or_404(Artwork, pk=pk, owner=request.user)
    if art.status != Artwork.Status.APPROVED:
        messages.error(request, "Возобновить продажу можно только для одобренного артворка.")
        return redirect(reverse('portfolio') + '?tab=on_sale')
    if not art.is_active:
        art.is_active = True
        art.save(update_fields=["is_active"])
        messages.success(request, f"«{art.title}» снова в продаже.")
    return redirect(reverse('portfolio') + '?tab=on_sale')


@login_required
def submit_for_review(request, pk):
    art = get_object_or_404(
        Artwork,
        pk=pk,
        owner=request.user,
        status=Artwork.Status.DRAFT
    )

    if art.pages.count() == 0:
        messages.error(request, "Нельзя отправить артворк без загруженных страниц.")
        return redirect('artwork_detail', pk=pk)

    art.status = Artwork.Status.PENDING
    art.save(update_fields=['status'])

    if art.status != Artwork.Status.DRAFT:
        messages.warning(request, "Этот артворк уже отправлен на модерацию.")
        return redirect('portfolio')

    # Собираем ссылки на оригиналы/цензуру, если нужно
    originals = [p.image.url for p in art.pages.all()]
    censored  = [p.censored_image.url for p in art.pages.all() if p.censored_image]

    if art.pages.count() == 0:
        messages.error(request, "Нельзя отправить артворк без загруженных страниц.")
        return redirect('artwork_detail', pk=pk)

    # Отправляем админу (если задан ADMINS)
    if getattr(settings, 'ADMINS', None):
        admin_list = [email for (_name, email) in settings.ADMINS]
        subject = f"Новый артворк на модерации: {art.title}"
        body = (
            f"Пользователь «{request.user.username}» отправил артворк «{art.title}».\n\n"
            "Оригиналы:\n" + "\n".join(originals) + "\n\n"
            "Цензурные (если есть):\n" + "\n".join(censored) + "\n\n"
            f"Ссылка в админку: {request.build_absolute_uri(f'/admin/users/artwork/{art.id}/change/')}"
        )
        msg = EmailMessage(subject, body, from_email=None, to=admin_list)
        msg.send(fail_silently=True)

    # При желании можно уведомить и автора в случае изменения статуса:
    recipient = [art.owner.email]
    subject2 = f"Артворк «{art.title}» принят на модерацию"
    body2 = (
        f"Здравствуйте, {art.owner.username}!\n\n"
        f"Ваш артворк «{art.title}» переведён в статус «На проверке».\n"
        "Как только модератор вынесет решение, вы получите уведомление."
    )
    msg2 = EmailMessage(subject2, body2, from_email=None, to=recipient)
    msg2.send(fail_silently=True)

    Notification.objects.create(
       user=request.user,
       message=f'Ваш артворк «{art.title}» отправлен на проверку модератору.',
       link=reverse('portfolio') + '?tab=pending'
    )

    messages.success(request, "Ваш артворк отправлен на проверку модератору.")
    return redirect('portfolio')


@login_required
@csrf_exempt
def save_censored_page(request, page_id):
    """
    Альтернативный эндпоинт для сохранения цензурной картинки (если нужен).
    """
    if request.method != 'POST':
        return HttpResponseBadRequest("Только POST разрешён")

    page = get_object_or_404(ArtworkImage, pk=page_id)
    art  = page.artwork
    if request.user != art.owner:
        return HttpResponseForbidden("У вас нет прав редактировать этот артворк")

    data_url = request.POST.get('editedImage')
    if not data_url or ';base64,' not in data_url:
        return HttpResponseBadRequest("Неверный формат данных изображения")

    header, b64data = data_url.split(';base64,')
    mime = header.split(':')[-1]    # например "image/png"
    ext  = mime.split('/')[-1]      # "png" или "jpeg"

    try:
        decoded = base64.b64decode(b64data)
    except (TypeError, ValueError):
        return HttpResponseBadRequest("Ошибочные base64-данные")

    filename = f'censored_{page.pk}.{ext}'
    if page.censored_image:
        page.censored_image.delete(save=False)
    page.censored_image.save(filename, ContentFile(decoded), save=True)

    return JsonResponse({
        'success': True,
        'url':     page.censored_image.url
    })


@login_required
def serve_full_page(request, page_id):
    """
    Отдаёт оригинал страницы только если пользователь купил артворк.
    """
    page = get_object_or_404(ArtworkImage, pk=page_id)
    art  = page.artwork
    # Предполагаем, что у вас есть модель Order с полем user и foreignkey на Artwork
    if request.user == art.owner or ArtworkOrder.objects.filter(artwork=art, user=request.user).exists():
        # Используем X-Sendfile или отдаём через Django, если небезопасно
        from django.http import FileResponse
        return FileResponse(page.image.open('rb'), content_type='image/jpeg')
    return HttpResponseForbidden()


def _to_dec_or_none(s):
    if s is None or str(s).strip() == "":
        return None
    try:
        return Decimal(str(s).strip())
    except (InvalidOperation, ValueError):
        return None


def artwork_store(request):
    now = timezone.now()
    qs = (
        Artwork.objects
        .filter(
            status=Artwork.Status.APPROVED,
            is_active=True,
            available_copies__gt=0,
        )
        .filter(Q(blocked_until__isnull=True) | Q(blocked_until__lt=now))
        .select_related('owner__profile')                 
        .annotate(avg_rating=Avg('ratings__rating')) 
    )
    # ---- фильтры
    q    = (request.GET.get("q") or "").strip()
    cat  = (request.GET.get("cat") or "").strip()
    nsfw = request.GET.get("nsfw")  # "", "1", "only"
    ccy  = (request.GET.get("ccy") or "").strip()
    pmin_raw = request.GET.get("price_min")
    pmax_raw = request.GET.get("price_max")
    sort = (request.GET.get("sort") or "new").strip()

    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(keywords__icontains=q))
    if cat:
        qs = qs.filter(category=cat)

    # NSFW:
    # "" (дефолт) -> только SFW; "1" -> SFW+NSFW; "only" -> только NSFW
    if nsfw == "only":
        qs = qs.filter(nsfw=True)
    elif nsfw == "1":
        pass  # показываем всё
    else:
        qs = qs.filter(nsfw=False)

    if ccy:
        qs = qs.filter(currency=ccy)

    # Цена: нормализация и защита от отрицательных/битых значений
    pmin = _to_dec_or_none(pmin_raw)
    pmax = _to_dec_or_none(pmax_raw)

    if pmin is not None and pmin < 0:
        pmin = Decimal("0")
    if pmax is not None and pmax < 0:
        pmax = Decimal("0")

    if pmin is not None and pmax is not None and pmin > pmax:
        pmin, pmax = pmax, pmin

    if pmin is not None:
        qs = qs.filter(price__gte=pmin)
    if pmax is not None:
        qs = qs.filter(price__lte=pmax)

    # ---- сортировка
    if sort == "price_asc":
        qs = qs.order_by("price", "-approved_at")
    elif sort == "price_desc":
        qs = qs.order_by("-price", "-approved_at")
    else:
        qs = qs.order_by("-approved_at", "-id")

    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get("page", 1))

    categories = [("cg","CG"), ("painting","Живопись"), ("drawing","Рисунок"), ("photo","Фотография"), ("digital","Цифровая иллюстрация")]
    currencies = [("OSP","OSP"), ("USDT","USDT")]

    return render(request, "artwork_store.html", {
        "page": page,
        "q": q, "cat": cat, 
        "nsfw": nsfw or "", 
        "ccy": ccy,
        "pmin": pmin_raw, 
        "pmax": pmax_raw, 
        "sort": sort,
        "categories": categories, 
        "currencies": currencies,
    })



@login_required
def view_artwork_public(request, artwork_id):
    from django.shortcuts import get_object_or_404, render, redirect
    from django.db.models import Avg
    from django.contrib.contenttypes.models import ContentType
    from django.conf import settings
    from .models import Artwork, Purchase, ArtworkOrder, ArtworkRating, Escrow

    artwork = get_object_or_404(Artwork, pk=artwork_id)

    if artwork.owner == request.user:
        return redirect('view_artwork_private', artwork_id=artwork_id)

    # 1) База: покупка завершена (офчейн или ончейн)
    base_purchase = (
        Purchase.objects.filter(user=request.user, artwork=artwork).exists()
        or ArtworkOrder.objects.filter(
            user=request.user, artwork=artwork,
            status__in=[ArtworkOrder.Status.COMPLETED, ArtworkOrder.Status.RELEASED],
        ).exists()
    )

    # 2) Эскроу: доступ в момент HELD (деньги зарезервированы)
    last_order = ArtworkOrder.objects.filter(user=request.user, artwork=artwork).order_by('-id').first()
    escrow = None
    seconds_to_autorelease = None
    access_by_escrow = False
    if last_order:
        ct = ContentType.objects.get_for_model(ArtworkOrder)
        escrow = Escrow.objects.filter(order_ct=ct, order_id=last_order.id).first()
        if escrow:
            seconds_to_autorelease = escrow.seconds_to_autorelease
            # Если средства удержаны — уже показываем полный контент
            access_by_escrow = (escrow.status == Escrow.Status.HELD)

    has_access = (request.user.is_staff or base_purchase or access_by_escrow)

    pages = artwork.pages.order_by('order').all()
    avg_rating = artwork.ratings.aggregate(avg=Avg('rating'))['avg']
    user_rating = ArtworkRating.objects.filter(artwork=artwork, user=request.user)\
        .values_list('rating', flat=True).first()

    return render(request, 'artwork_detail_public.html', {
        'artwork': artwork,
        'has_access': has_access,
        'pages': pages,
        'order': last_order,
        'escrow': escrow,
        'avg_rating': avg_rating,
        'user_rating': user_rating,
        'seconds_to_autorelease': seconds_to_autorelease,
        'CHAIN_ID': settings.CHAIN_ID,
        'ESCROW_ADDRESS': settings.ESCROW_CONTRACT_ADDRESS,
    })



@login_required
def rate_artwork(request, artwork_id):
    artwork = get_object_or_404(Artwork, id=artwork_id)

    # доступ к оценке — только покупателю арта
    has_purchased = (
        Purchase.objects.filter(user=request.user, artwork=artwork).exists()  # на случай оффчейн-ветки/legacy
        or ArtworkOrder.objects.filter(
            user=request.user,
            artwork=artwork,
            status__in=[ArtworkOrder.Status.COMPLETED, ArtworkOrder.Status.RELEASED]
        ).exists()
    )
    if not has_purchased:
        messages.error(request, "Оценку может оставить только покупатель.")
        return redirect('view_artwork_public', artwork_id=artwork.id)

    # один раз
    if ArtworkRating.objects.filter(artwork=artwork, user=request.user).exists():
        messages.info(request, "Вы уже оценили этот артворк, изменить оценку нельзя.")
        return redirect('view_artwork_public', artwork_id=artwork.id)

    if request.method == 'POST':
        try:
            rating_val = int(request.POST.get('rating'))
        except (ValueError, TypeError):
            messages.error(request, "Некорректная оценка.")
            return redirect('view_artwork_public', artwork_id=artwork.id)

        if 1 <= rating_val <= 5:
            ArtworkRating.objects.create(artwork=artwork, user=request.user, rating=rating_val)
            messages.success(request, "Спасибо за вашу оценку!")
        else:
            messages.error(request, "Оценка должна быть от 1 до 5.")

    return redirect('view_artwork_public', artwork_id=artwork.id)


@login_required
def view_artwork_private(request, artwork_id):
    artwork = get_object_or_404(Artwork, pk=artwork_id, owner=request.user)

    pages = artwork.pages.order_by('order').all()

    return render(request, 'artwork_detail_private.html', {
        'artwork': artwork,
        'pages': pages,
        'has_access': True  # доступ ко всему содержимому
    })

@login_required
@require_POST
def confirm_art_order(request, order_id: int):

    order = get_object_or_404(ArtworkOrder, pk=order_id, user=request.user)
    ct = ContentType.objects.get_for_model(ArtworkOrder)
    esc = get_object_or_404(Escrow, order_ct=ct, order_id=order.id)

    # уже обработанные
    if order.status in (ArtworkOrder.Status.COMPLETED, ArtworkOrder.Status.RELEASED):
        messages.info(request, "Заказ уже обработан.")
        return redirect("view_artwork_public", artwork_id=order.artwork_id)

    currency = getattr(order.artwork, "currency", None)

    # === OSP (off-chain): здесь и надо закрыть escrow локально ===
    if currency == Currency.OSP:
        if esc.status == Escrow.Status.HELD:
            with transaction.atomic():
                # 1) ставим RELEASED у escrow
                esc.status = Escrow.Status.RELEASED
                esc.released_at = timezone.now()
                esc.disputed = False
                esc.moderator_locked = False
                esc.save(update_fields=["status", "released_at", "disputed", "moderator_locked"])

                # 2) уменьшаем тираж (при наличии)
                art = Artwork.objects.select_for_update().get(pk=order.artwork_id)
                if art.available_copies <= 0:
                    messages.error(request, "К сожалению, копии закончились. Заказ отменён.")
                    return redirect("view_artwork_public", artwork_id=art.id)
                Artwork.objects.filter(pk=art.pk, available_copies__gte=1).update(
                    available_copies=F("available_copies") - 1
                )

                # 3) отмечаем заказ завершённым
                order.status = ArtworkOrder.Status.COMPLETED
                order.save(update_fields=["status"])

                # 4) проводим деньги продавцу с комиссией платформы (если используешь отдельную задачу)
                # settle_osp_release_with_fee.delay(
                #     seller_id=art.owner_id,
                #     total_amount_micros=int(esc.amount_micros),
                #     escrow_id=esc.id,
                #     order_ref=f"artwork_order:{order.id}",
                # )

            messages.success(request, "Покупка подтверждена, доступ открыт.")
            return redirect("view_artwork_public", artwork_id=order.artwork_id)

        # если уже RELEASED — добиваем заказ как завершённый
        if esc.status == Escrow.Status.RELEASED:
            with transaction.atomic():
                art = Artwork.objects.select_for_update().get(pk=order.artwork_id)
                if art.available_copies > 0:
                    Artwork.objects.filter(pk=art.pk, available_copies__gte=1).update(
                        available_copies=F("available_copies") - 1
                    )
                order.status = ArtworkOrder.Status.COMPLETED
                order.save(update_fields=["status"])
            messages.success(request, "Покупка подтверждена.")
            return redirect("view_artwork_public", artwork_id=order.artwork_id)

        messages.warning(request, f"Эскроу в статусе {esc.status}.")
        return redirect("view_artwork_public", artwork_id=order.artwork_id)

    # === USDT (on-chain): подтверждение только транзакцией release() ===
    if esc.status == Escrow.Status.HELD:
        messages.info(
            request,
            "Для USDT нужно выполнить on-chain release(). Статус обновится автоматически после подтверждения сети."
        )
        return redirect("view_artwork_public", artwork_id=order.artwork_id)

    if esc.status == Escrow.Status.RELEASED:
        with transaction.atomic():
            art = Artwork.objects.select_for_update().get(pk=order.artwork_id)
            if art.available_copies > 0:
                Artwork.objects.filter(pk=art.pk, available_copies__gte=1).update(
                    available_copies=F("available_copies") - 1
                )
            order.status = ArtworkOrder.Status.COMPLETED
            order.save(update_fields=["status"])
        messages.success(request, "Покупка подтверждена.")
        return redirect("view_artwork_public", artwork_id=order.artwork_id)

    messages.warning(request, f"Эскроу в статусе {esc.status}.")
    return redirect("view_artwork_public", artwork_id=order.artwork_id)


@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def confirm_text_purchase(request, product_id):
    product = get_object_or_404(
        TextProduct, pk=product_id,
        status=TextProduct.Status.APPROVED, is_active=True, is_deleted=False
    )

    # только OSP подтверждаем здесь (USDT — ончейн-страница)
    if product.currency != Currency.OSP:
        messages.warning(request, "Подтверждение доступно только для покупок за OSP.")
        return redirect("public_view_text_product", product_id=product.id)

    # защита от повторной покупки
    if TextProductOrder.objects.filter(user=request.user, product=product, is_active=True).exists():
        messages.info(request, "Вы уже купили этот продукт.")
        return redirect("public_view_text_product", product_id=product.id)

    if request.method == "GET":
        form = ConfirmPurchaseForm()
        return render(request, "purchase_confirm.html", {
            "kind": "text",
            "object": product,
            "price": product.price,
            "currency": product.currency,
            "form": form,
            "rules_url": "/rules/",       # при желании вынеси в settings
            "policy_url": "/security/",   # при желании вынеси в settings
        })

    # POST
    form = ConfirmPurchaseForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Нужно подтвердить согласие с правилами.")
        return render(request, "purchase_confirm.html", {
            "kind": "text",
            "object": product,
            "price": product.price,
            "currency": product.currency,
            "form": form,
            "rules_url": "/rules/",
            "policy_url": "/security/",
        })

    # подтверждено — оформляем покупку единым путём
    result = process_purchase(request.user, TextProduct, product.id, currency=product.currency)

    if isinstance(result, HttpResponse):
        # аккуратно показать причину
        try:
            reason = result.content.decode("utf-8").strip()
        except Exception:
            reason = ""
        if result.status_code == 403 and reason:
            messages.error(request, reason)
        elif result.status_code == 403:
            messages.error(request, "Недостаточно средств или нет доступа к покупке.")
        else:
            messages.error(request, "Не удалось оформить покупку.")
        return redirect("public_view_text_product", product_id=product.id)

    if result is None:
        messages.info(request, "Вы уже купили этот продукт.")
        return redirect("public_view_text_product", product_id=product.id)

    messages.success(request, "Покупка оформлена: средства зарезервированы в эскроу.")
    return redirect("public_view_text_product", product_id=product.id)


@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def confirm_artwork_purchase(request, artwork_id):
    art = get_object_or_404(
        Artwork, pk=artwork_id,
        is_approved=True, is_active=True, available_copies__gt=0
    )

    if art.currency != Currency.OSP:
        messages.warning(request, "Подтверждение доступно только для покупок за OSP.")
        return redirect("view_artwork_public", artwork_id=art.id)

    if ArtworkOrder.objects.filter(user=request.user, artwork=art).exists():
        messages.info(request, "Вы уже купили этот артворк.")
        return redirect("view_artwork_public", artwork_id=art.id)

    if request.method == "GET":
        form = ConfirmPurchaseForm()
        return render(request, "purchase_confirm.html", {
            "kind": "art",
            "object": art,
            "price": art.price,
            "currency": art.currency,
            "form": form,
            "rules_url": "/rules/",
            "policy_url": "/security/",
        })

    form = ConfirmPurchaseForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Нужно подтвердить согласие с правилами.")
        return render(request, "purchase_confirm.html", {
            "kind": "art",
            "object": art,
            "price": art.price,
            "currency": art.currency,
            "form": form,
            "rules_url": "/rules/",
            "policy_url": "/security/",
        })

    result = process_purchase(request.user, Artwork, art.id, currency=art.currency)

    if isinstance(result, HttpResponse):
        try:
            reason = result.content.decode("utf-8").strip()
        except Exception:
            reason = ""
        if result.status_code == 403 and reason:
            messages.error(request, reason)
        elif result.status_code == 403:
            messages.error(request, "Недостаточно средств или нет доступа к покупке.")
        else:
            messages.error(request, "Не удалось оформить покупку.")
        return redirect("view_artwork_public", artwork_id=art.id)

    if result is None:
        messages.info(request, "Вы уже купили этот артворк.")
        return redirect("view_artwork_public", artwork_id=art.id)

    messages.success(request, "Покупка оформлена: средства зарезервированы в эскроу.")
    return redirect("view_artwork_public", artwork_id=art.id)



@login_required
@require_POST
def dispute_art_order(request, order_id: int):
    return redirect(f"{reverse('refunds_hub')}?type=art&order={order_id}")


@login_required
@require_POST
@transaction.atomic
def buy_artwork(request, artwork_id):
    user = request.user

    # 1) Получаем артворк (только доступные к продаже копии)
    art = get_object_or_404(
        Artwork,
        pk=artwork_id,
        is_approved=True,
        is_active=True,
        available_copies__gt=0,
    )

    # 2) Защита от повторной покупки (доп.страховка; внутри process_purchase тоже есть проверка)
    if ArtworkOrder.objects.filter(user=user, artwork=art).exists():
        messages.info(request, "Вы уже купили этот артворк.")
        return redirect('view_artwork_public', artwork_id=art.id)

    # 3) Единый путь оформления покупки (OSP оффчейн и USDT ончейн)
    #    Важно: НЕ создаём Order/Escrow/Tx вручную и НЕ уменьшаем available_copies здесь.
    from .purchase import process_purchase
    result = process_purchase(user, Artwork, art.id, currency=art.currency)

    # process_purchase может вернуть HttpResponse(403/400) с причиной
    if isinstance(result, HttpResponse):
        try:
            reason = result.content.decode('utf-8').strip()
        except Exception:
            reason = ""
        if result.status_code == 403 and reason:
            messages.error(request, reason)
        elif result.status_code == 403:
            messages.error(request, "Недостаточно средств или нет доступа к покупке.")
        else:
            messages.error(request, "Не удалось оформить покупку.")
        return redirect('view_artwork_public', artwork_id=art.id)

    # Если None — заказ уже существовал (гонка/повтор)
    if result is None:
        messages.info(request, "Вы уже купили этот артворк.")
        return redirect('view_artwork_public', artwork_id=art.id)

    # 4) Успех: для OSP — средства списаны и удержаны в Escrow(HELD) синхронно.
    #    Для USDT — создан заказ+эскроу с адресами; далее пользователь увидит инструкции.
    if art.currency == Currency.USDT:
        # Если вы хотите сохранить текущую страницу с инструкциями —
        # можно отрендерить её здесь, используя созданный order / escrow.
        messages.success(request, "Заказ создан. Следуйте инструкциям по оплате USDT.")
        return redirect('view_artwork_public', artwork_id=art.id)

    # OSP: просто сообщаем об удержании в эскроу
    messages.success(request, "Покупка оформлена: средства зарезервированы в эскроу.")
    return redirect('view_artwork_public', artwork_id=art.id)


def _find_order_and_escrow(kind: str, obj_id: int, user):
    if kind == "text":
        order = TextProductOrder.objects.filter(user=user, product_id=obj_id).order_by('-id').first()
    elif kind == "art":
        order = ArtworkOrder.objects.filter(user=user, artwork_id=obj_id).order_by('-id').first()
    else:
        order = None
    escrow = getattr(order, 'escrow', None) if order else None
    return order, escrow

@login_required
@require_POST
def open_dispute(request):
    """
    body: { kind: "text"|"art", id: <int>, reason?: str, evidence_url?: str }
    """
    data = request.POST
    kind = data.get("kind")
    obj_id = int(data.get("id", 0))
    reason = (data.get("reason") or "").strip()
    evidence_url = (data.get("evidence_url") or "").strip()

    if kind not in ("text","art") or obj_id <= 0:
        return HttpResponseBadRequest("bad args")

    order, escrow = _find_order_and_escrow(kind, obj_id, request.user)
    if not escrow or escrow.status != Escrow.Status.HELD:
        return HttpResponseBadRequest("escrow not found or not HELD")

    with transaction.atomic():
        # стопорим авто-релиз и помечаем спор
        escrow.disputed = True
        escrow.moderator_locked = True
        escrow.save(update_fields=["disputed", "moderator_locked"])

        d, created = Dispute.objects.get_or_create(
            escrow=escrow,
            defaults={"opened_by": request.user, "reason": reason, "evidence_url": evidence_url},
        )
        if not created:
            # обновим причину/доказательства, если уже есть
            d.reason = reason or d.reason
            d.evidence_url = evidence_url or d.evidence_url
            d.status = Dispute.Status.OPENED
            d.save(update_fields=["reason", "evidence_url", "status"])

    return JsonResponse({"ok": True, "dispute_id": d.id})

@login_required
@require_POST
def cancel_dispute(request):
    """
    Покупатель может снять спор до решения модератора (опционально).
    """
    data = request.POST
    kind = data.get("kind")
    obj_id = int(data.get("id", 0))
    if kind not in ("text","art") or obj_id <= 0:
        return HttpResponseBadRequest("bad args")

    order, escrow = _find_order_and_escrow(kind, obj_id, request.user)
    if not escrow or not getattr(escrow, "dispute", None):
        return HttpResponseBadRequest("no dispute")

    d = escrow.dispute
    if not d.is_open():
        return HttpResponseBadRequest("already resolved")

    with transaction.atomic():
        # снимаем флаги, возвращаем к обычному авто-релизу
        escrow.disputed = False
        escrow.moderator_locked = False
        escrow.save(update_fields=["disputed", "moderator_locked"])
        d.status = Dispute.Status.RESOLVED
        d.moderator_decision = "CANCELED"
        d.decided_at = timezone.now()
        d.save(update_fields=["status", "moderator_decision", "decided_at"])
    return JsonResponse({"ok": True})

def staff_required(user):
    return user.is_staff or user.is_superuser

def purchase_success(request, order_id: int):
    """
    Страница подтверждения покупки.
    Сейчас редиректится сюда ветка покупки арта за OSP.
    Показываем краткую информацию о заказе и статус эскроу.
    """
    # сперва пытаемся найти заказ по артам
    order = (ArtworkOrder.objects
             .select_related('artwork', 'user')
             .filter(id=order_id, user=request.user)
             .first())

    # при желании — поддержим и текстовые заказы тем же экраном
    if not order:
        text_order = (TextProductOrder.objects
                      .select_related('product', 'user')
                      .filter(id=order_id, user=request.user)
                      .first())
        if text_order:
            # можно отрендерить тот же шаблон с флагом "text"
            return render(request, 'purchase_success.html', {
                'order': text_order,
                'is_text_order': True,
            })
        # ничего не нашли
        return HttpResponseNotFound("Заказ не найден")

    return render(request, 'purchase_success.html', {
        'order': order,
        'is_text_order': False,
    })


@login_required
@require_POST
def prepare_order(request):
    """
    Вход:  kind = "text"|"art", id = <int>
    Выход: { orderId, tokenAddress, amountBaseUnits, chainId, escrowAddress }
    ВАЖНО: для ончейн допускаем только USDT.
    """
    kind = request.POST.get("kind")
    try:
        pid = int(request.POST.get("id", "0"))
    except ValueError:
        return HttpResponseBadRequest("bad id")

    if kind not in ("text", "art") or pid <= 0:
        return HttpResponseBadRequest("bad args")

    # --- общий загруз и проверки по сущности ---
    if kind == "text":
        product = get_object_or_404(TextProduct.objects.select_related("owner"), pk=pid, is_active=True)
        if product.owner_id == request.user.id:
            return HttpResponseBadRequest("owner cannot buy own product")

        price = Decimal(product.price)
        currency = product.currency  # 'USDT' | 'OSP' | 'USD' ...
        # одна покупка текста на пользователя
        if TextProductOrder.objects.filter(user=request.user, product_id=product.id).exists():
            return HttpResponseBadRequest("already purchased")

    else:  # art
        product = get_object_or_404(Artwork.objects.select_related("owner"), pk=pid, is_active=True)
        if product.owner_id == request.user.id:
            return HttpResponseBadRequest("owner cannot buy own artwork")

        if getattr(product, "available_copies", 0) <= 0:
            return HttpResponseBadRequest("no copies available")

        price = Decimal(product.price)
        currency = product.currency  # 'USDT' | 'OSP' | 'USD' ...

    # --- ончейн допускаем только USDT ---
    if currency != Currency.USDT:
        return HttpResponseBadRequest("on-chain allowed only for USDT")



    # --- адрес токена и decimals строго с сервера ---
    token_addr = (TOKEN_ADDRESS.get("USDT") if "TOKEN_ADDRESS" in globals() else getattr(settings, "USDT_TOKEN_ADDRESS", ""))
    if not token_addr:
        return HttpResponseBadRequest("token address not configured")

    decimals = (TOKEN_DECIMALS.get("USDT") if "TOKEN_DECIMALS" in globals() else int(getattr(settings, "USDT_DECIMALS", 6)))
    try:
        decimals = int(decimals)
    except Exception:
        decimals = 6

    amount_base = to_base_units(price, decimals)
    if amount_base <= 0:
        return HttpResponseBadRequest("bad amount")


    # --- генерим orderId и сохраняем в черновик заказа (как у тебя) ---
    order_id_hex = make_order_id_hex(request.user.id, kind, product.id)

    if kind == "text":
        order, _created = TextProductOrder.objects.get_or_create(
            user=request.user, product=product,
            defaults={"price": price, "is_active": False}
        )
        # связываем внешний id
        if not getattr(order, "external_order_id", None):
            order.external_order_id = order_id_hex
            order.save(update_fields=["external_order_id"])
    else:
        order = ArtworkOrder.objects.create(
            user=request.user, artwork=product,
            amount=0  # заполним по факту, если где-то нужно
        )
        order.external_order_id = order_id_hex
        order.save(update_fields=["external_order_id"])

    # --- отдаём на фронт только то, что должно прийти от сервера ---
    return JsonResponse({
        "orderId": order_id_hex,
        "tokenAddress": token_addr,
        "amountBaseUnits": str(amount_base),               # отдаем строкой
        "chainId": int(getattr(settings, "CHAIN_ID", 0)),
        "escrowAddress": getattr(settings, "ESCROW_CONTRACT_ADDRESS", ""),
        # "seller": product.owner_id,  # фронту обычно не нужен, можно не отдавать
    })



@login_required
@require_POST
def confirm_order(request):
    """
    body: {
      "kind": "text"|"art",
      "id": <int>,
      "orderId": "0x..64",   # external_order_id
      "txHash":  "0x..64",   # hash депозита в эскроу
      "chainId": <int>
    }
    """
    data = request.POST or getattr(request, "json", None) or {}
    kind = (data.get("kind") or "").strip()
    pid  = int(data.get("id") or 0)
    order_id = (data.get("orderId") or "").strip()
    txh = (data.get("txHash") or "").strip()
    client_chain = int(data.get("chainId") or 0)

    if kind not in ("text", "art") or pid <= 0:
        return HttpResponseBadRequest("bad args")

    if not ORDER_ID_RE.fullmatch(order_id) or not TX_HASH_RE.fullmatch(txh):
        return HttpResponseBadRequest("bad format")

    if client_chain != getattr(settings, "CHAIN_ID", 0):
        return HttpResponseBadRequest("wrong chain")

    with transaction.atomic():
        if kind == "text":
            o = TextProductOrder.objects.select_for_update().get(
                user=request.user, product_id=pid, external_order_id=order_id
            )
            # для депозита всегда пишем в deposit_tx
            o.deposit_tx = txh
            o.save(update_fields=["deposit_tx"])
        else:
            o = ArtworkOrder.objects.select_for_update().get(
                user=request.user, artwork_id=pid, external_order_id=order_id
            )
            # НЕ трогаем o.tx_hash (это про createOrder); депозит ложим в deposit_tx
            o.deposit_tx = txh
            o.save(update_fields=["deposit_tx"])

    return JsonResponse({"ok": True})



# Образовательные уроки
@login_required
def add_lesson(request):
    if request.method == 'POST':
        form = LessonForm(request.POST)
        if form.is_valid():
            lesson = form.save(commit=False)
            lesson.teacher = request.user
            lesson.save()
            messages.success(request, "Урок успешно добавлен.")
            return redirect('dashboard')
    else:
        form = LessonForm()
    return render(request, 'add_lesson.html', {'form': form})

@login_required
def edit_lesson(request, lesson_id):
    # Убедимся, что урок принадлежит текущему пользователю
    lesson = get_object_or_404(Lesson, id=lesson_id, teacher=request.user)

    if request.method == 'POST':
        # Если нажали кнопку удаления
        if 'delete' in request.POST:
            lesson.delete()
            messages.success(request, "Урок успешно удалён.")
            return redirect('lesson_list')

        # Иначе — обычное обновление
        form = LessonForm(request.POST, instance=lesson)
        if form.is_valid():
            form.save()
            messages.success(request, "Урок успешно обновлён.")
            return redirect('lesson_list')
    else:
        form = LessonForm(instance=lesson)

    return render(request, 'edit_lesson.html', {
        'form': form,
        'lesson': lesson,
    })


@login_required
def enroll_lesson(request, lesson_id):
    lesson = get_object_or_404(Lesson, id=lesson_id)
    if request.user not in lesson.students.all():
        lesson.students.add(request.user)
        messages.success(request, f"Вы записались на урок {lesson.title}!")
    else:
        messages.info(request, "Вы уже записаны на этот урок.")
    return redirect('dashboard')


def _norm_text(p: TextProduct):
    return {
        "type": "text",
        "id": p.pk,
        "title": p.title,
        "status": p.status,                    # draft/pending/approved/rejected
        "is_active": bool(getattr(p, "is_active", False)),
        "created_at": getattr(p, "created_at", None),
        "price": p.price,
        "currency": p.currency or "OSP",
    }

def _norm_art(a: Artwork):
    # created_at: берём created_at если есть, иначе created
    ca = getattr(a, "created_at", None) or getattr(a, "created", None)
    return {
        "type": "art",
        "id": a.pk,
        "title": a.title,
        "status": a.status,                    # draft/pending/approved/rejected
        "is_active": bool(getattr(a, "is_active", False)),
        "created_at": ca,
        "price": a.price,
        "currency": a.currency or "OSP",
    }

@login_required
def portfolio(request):
    # Текущая вкладка; мягкая совместимость со старыми ссылками
    tab = request.GET.get("tab", "drafts")
    if tab in ("pending", "rejected"):
        tab = "review"

    user = request.user

    q = (request.GET.get("q") or "").strip()
    sort_param = request.GET.get("sort") or ""

    # === ТЕКСТЫ (без soft-delete)
    tp_qs = TextProduct.objects.filter(owner=user, is_deleted=False)

    drafts_texts   = tp_qs.filter(status=TextProduct.Status.DRAFT)
    pending_texts  = tp_qs.filter(status=TextProduct.Status.PENDING)
    rejected_texts = tp_qs.filter(status=TextProduct.Status.REJECTED)
    on_sale_texts  = tp_qs.filter(status=TextProduct.Status.APPROVED)

    # === АРТВОРКИ
    drafts_art   = Artwork.objects.filter(owner=user, status=Artwork.Status.DRAFT,    is_deleted=False)
    pending_art  = Artwork.objects.filter(owner=user, status=Artwork.Status.PENDING,  is_deleted=False)
    rejected_art = Artwork.objects.filter(owner=user, status=Artwork.Status.REJECTED, is_deleted=False)
    on_sale_art  = Artwork.objects.filter(owner=user, status=Artwork.Status.APPROVED, is_active=True, is_deleted=False)

    # === Нормализация списков (каждый элемент имеет "type": "text"/"art")
    drafts  = list(map(_norm_text, drafts_texts)) + list(map(_norm_art, drafts_art))
    on_sale = list(map(_norm_text, on_sale_texts)) + list(map(_norm_art, on_sale_art))

    # Единая вкладка «Модерация»: pending + rejected
    review_pending  = list(map(_norm_text, pending_texts))  + list(map(_norm_art, pending_art))
    review_rejected = list(map(_norm_text, rejected_texts)) + list(map(_norm_art, rejected_art))
    for it in review_pending:
        it["moderation_state"] = "pending"
    for it in review_rejected:
        it["moderation_state"] = "rejected"
    review = review_pending + review_rejected

    # === Приобретённые
    purchased_texts    = TextProduct.objects.filter(orders__user=user).distinct()
    purchased_artworks = Artwork.objects.filter(orders__user=user).distinct()
    purchased = list(map(_norm_text, purchased_texts)) + list(map(_norm_art, purchased_artworks))

    # === Контекст
    context = {
        "tab": tab,

        "drafts": drafts,
        "on_sale": on_sale,
        "review": review,          # объединённая вкладка «Модерация»
        "purchased": purchased,    # единый список покупок
        "q": q,
        "sort": sort_param,
        "drafts_count": len(drafts),
        "on_sale_count": len(on_sale),
        "review_count": len(review),
    }
    return render(request, "portfolio.html", context)




@login_required
def edit_text_product(request, pk):
    prod = get_object_or_404(
        TextProduct,
        pk=pk,
        owner=request.user,
        status=TextProduct.Status.DRAFT  # редактируем только черновики
    )

    if request.method == 'POST':
        form = TextProductForm(request.POST, instance=prod)
        if 'save_later' in request.POST:
            if form.is_valid():
                form.save(owner=request.user)  # внутри формы произойдёт шифрование
                messages.success(request, "Черновик обновлён.")
                return HttpResponseRedirect(reverse('portfolio') + '#drafts')
            return render(request, 'add_text_product.html', {'form': form, 'is_edit': True, 'product': prod}, status=400)

        if 'submit_review' in request.POST:
            if form.is_valid():
                prod = form.save(owner=request.user)
                prod.submit_for_review()
                messages.success(request, "Продукт отправлен на проверку.")
                return HttpResponseRedirect(reverse('portfolio') + '#pending')
            return render(request, 'add_text_product.html', {'form': form, 'is_edit': True, 'product': prod}, status=400)

        # Любой другой POST — показать ошибки
        return render(request, 'add_text_product.html', {'form': form, 'is_edit': True, 'product': prod}, status=400)

    # GET — заполняем форму расшифрованным контентом
    initial = {'content': prod.decrypt_content() or ''}
    form = TextProductForm(instance=prod, initial=initial)
    return render(request, 'add_text_product.html', {'form': form, 'is_edit': True, 'product': prod})


@login_required
def notifications_list(request):
    nots = request.user.notifications.all()
    # отмечаем прочитанными
    nots.filter(is_read=False).update(is_read=True)
    return render(request, 'notifications.html', {'notifications': nots})

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def api_create_purchase(request):
    data = request.data
    art  = Artwork.objects.get(pk=data['artwork_id'])
    ArtworkOrder.objects.create(
        user     = request.user,
        artwork  = art,
        external_order_id = data['order_id'],
        tx_hash  = data['tx_hash'],
        amount   = data['amount']
    )
    return Response({'status':'ok'})

@login_required
def donate_osp_view(request):
    presets = [5, 10, 25, 50, 100, 250, 500, 1000]
    if request.method == "GET":
        # сгенерим токен идемпотентности
        request.session["donate_token"] = uuid.uuid4().hex
        return render(request, "donate_osp.html", {
            "presets": presets,
            "token": request.session["donate_token"],  # чтобы вставить в hidden-поле
        })

@login_required
@require_POST
@transaction.atomic
def donate_osp_submit(request):
    presets = [5, 10, 25, 50, 100, 250, 500, 1000]
    try:
        token = request.POST.get("token", "")
        if not token or token != request.session.get("donate_token"):
            messages.error(request, "Сессия истекла, обновите страницу.")
            return redirect("donate_osp")

        # деноминируем в доллары с точностью до цента
        amt = Decimal(str(request.POST.get("amount", "0"))).quantize(Decimal("1.00"))
        if amt <= 0 or amt > Decimal("10000"):
            messages.error(request, "Некорректная сумма.")
            return render(request, "donate_osp.html",
                          {"presets": presets, "token": request.session.get("donate_token")}, status=400)

        # кошелёк
        wallet = request.user.crypto_wallets.get(currency=Currency.OSP)

        # расчёт суммы в микро и референс
        micros = usd_to_micros(amt)
        ref = uuid.uuid4().hex  # уникальный reference для идемпотентности

        # 1) Бухгалтерия (источник истины)
        book_osp(wallet,
                 kind=LedgerEntry.Kind.TOPUP_OSP_SOFT,
                 reference=ref,
                 delta_micros=micros)

        # 2) «Человеческая» история — идемпотентно
        CryptoTransaction.objects.get_or_create(
            wallet=wallet,
            tx_type="deposit",
            reference=ref,
            defaults={
                "amount": amt,
                "amount_micros": micros,
                "tx_hash": "",
            }
        )

        # погасим токен, чтобы повторный сабмит не зачислил повторно
        request.session.pop("donate_token", None)

        messages.success(request, "Средства зачислены на ваш OSP-кошелёк.")
        return redirect("wallets")

    except Exception as e:
        messages.error(request, f"Ошибка: {e}")
        # вернём токен в форму, чтобы не заставлять перегружать страницу
        return render(request, "donate_osp.html",
                      {"presets": presets, "token": request.session.get("donate_token")}, status=400)



@login_required
def vip_plans_view(request):
    plans = VIPPlan.objects.filter(is_active=True).order_by("price_osp_micros")
    current = VIPSubscription.objects.filter(user=request.user).order_by("-end_at").first()
    return render(request, "vip_plans.html", {"plans": plans, "current": current})

@login_required
def buy_vip_view(request, plan_id: int):
    plan = get_object_or_404(VIPPlan, pk=plan_id, is_active=True)
    # Важно: держим транзакцию до конца операции покупки
    with transaction.atomic():
        wallet = (request.user.crypto_wallets
                  .select_for_update()
                  .get(currency=Currency.OSP))

        cost_micros = int(plan.price_osp_micros)
        cost_dec = Decimal(cost_micros) / Decimal("1000000")

        try:
            # 1) Списание (леджер)
            book_osp(
                wallet,
                kind=LedgerEntry.Kind.PURCHASE_VIP,
                reference=f"vip:{plan.code}",
                delta_micros=-cost_micros,
            )

            # 2) Человекочитаемая запись истории кошелька
            CryptoTransaction.objects.create(
                wallet=wallet,
                tx_type="purchase",
                amount=cost_dec,
                amount_micros=cost_micros,
                reference=f"vip:{plan.code}",
            )

            # 3) Выдача/продление подписки
            sub = VIPSubscription.grant_or_extend(request.user, plan)
            settle_vip_revenue_to_platform(cost_micros, wallet, plan.code)
            
            # 4) Автоприменение «лучшего» VIP-цвета
            profile, _ = Profile.objects.get_or_create(user=request.user)
            cur = (profile.background_color or "").lower()
            if plan.level == "GALAXY":
                if cur not in ("vip-galaxy", "vip-star", "vip-gold"):
                    profile.background_color = "vip-galaxy"
            elif plan.level == "STAR":
                if cur not in ("vip-star", "vip-galaxy"):
                    profile.background_color = "vip-star"
            elif plan.level == "GOLD":
                if cur != "vip-gold":
                    profile.background_color = "vip-gold"
            profile.save(update_fields=["background_color"])

            messages.success(request, f"VIP активирован до {sub.end_at:%d.%m.%Y %H:%M}.")
        except ValueError:
            messages.error(request, "Недостаточно OSP.")
    return redirect("vip_plans")


def get_active_vip_plan(user):
    sub = (VIPSubscription.objects
           .select_related('plan')
           .filter(user=user, end_at__gt=timezone.now())
           .order_by('-end_at')
           .first())
    return sub.plan if sub else None


def check_creation_quota(user):
    """
    Возвращает (blocked: bool, until: datetime|None, reason: str)
    Правила:
      - Без VIP: 1 создание раз в 3 дня (72ч).
      - Gold:    1 создание раз в 2 дня (48ч).
      - Star:    1 создание в сутки (24ч).
      - Galaxy:  до 2 созданий в сутки.
    """
    if getattr(user, "is_staff", False):
        return (False, None, "")

    plan = get_active_vip_plan(user)
    now = timezone.now()

    if plan and plan.level == VIPPlan.Level.GALAXY:
        # 2 продукта в последние 24ч
        window_start = now - timedelta(hours=24)
        cnt_24h = CreationQuotaLog.objects.filter(user=user, created_at__gte=window_start).count()
        if cnt_24h >= 2:
            oldest = CreationQuotaLog.objects.filter(user=user, created_at__gte=window_start).order_by("created_at").first()
            until = oldest.created_at + timedelta(hours=24)
            return (True, until, "GALAXY daily quota 2/24h")
        return (False, None, "")

    if plan and plan.level == VIPPlan.Level.STAR:
        # 1 в 24ч
        last = CreationQuotaLog.objects.filter(user=user).order_by("-created_at").first()
        if last and last.created_at + timedelta(hours=24) > now:
            return (True, last.created_at + timedelta(hours=24), "STAR 1/24h")
        return (False, None, "")

    if plan and plan.level == VIPPlan.Level.GOLD:
        # 1 в 48ч
        last = CreationQuotaLog.objects.filter(user=user).order_by("-created_at").first()
        if last and last.created_at + timedelta(hours=48) > now:
            return (True, last.created_at + timedelta(hours=48), "GOLD 1/48h")
        return (False, None, "")

    # Нет VIP: 1 в 72ч
    last = CreationQuotaLog.objects.filter(user=user).order_by("-created_at").first()
    if last and last.created_at + timedelta(hours=72) > now:
        return (True, last.created_at + timedelta(hours=72), "BASIC 1/72h")
    return (False, None, "")



@login_required
def ledger_history(request):
    """
    История операций: фильтры по дате, типу, стороне, валюте и референсу.
    GET-параметры:
      q=... (подстрока в reference/tx_hash)
      kind=PURCHASE_VIP / TOPUP_OSP_SOFT / ...
      side=DEBIT|CREDIT
      currency=OSP|USDT
      from=YYYY-MM-DD
      to=YYYY-MM-DD
      export=csv  (если нужно выгрузить)
      page=N
    """
    qs = LedgerEntry.objects.filter(user=request.user).order_by("-created_at")

    # фильтры
    q = (request.GET.get("q") or "").strip()
    kind = (request.GET.get("kind") or "").strip()
    side = (request.GET.get("side") or "").strip()
    currency = (request.GET.get("currency") or "").strip()
    date_from = request.GET.get("from") or ""
    date_to   = request.GET.get("to") or ""

    if q:
        qs = qs.filter(Q(reference__icontains=q) | Q(external_tx_hash__icontains=q))

    if kind:
        qs = qs.filter(kind=kind)

    if side in ("DEBIT","CREDIT"):
        qs = qs.filter(side=side)

    if currency:
        qs = qs.filter(currency=currency)

    # даты (по дню)
    try:
        if date_from:
            dtf = datetime.fromisoformat(date_from)
            qs = qs.filter(created_at__date__gte=dtf.date())
        if date_to:
            dtt = datetime.fromisoformat(date_to)
            qs = qs.filter(created_at__date__lte=dtt.date())
    except Exception:
        pass

    # экспорт CSV
    if request.GET.get("export") == "csv":
        import csv
        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="ledger.csv"'
        w = csv.writer(resp)
        w.writerow(["created_at","side","kind","currency","amount_micros","balance_after_micros","reference","external_tx_hash"])
        for e in qs:
            w.writerow([
                e.created_at.isoformat(),
                e.side, e.kind, e.currency,
                e.amount_micros, e.balance_after_micros,
                e.reference, e.external_tx_hash
            ])
        return resp

    # пагинация
    page = int(request.GET.get("page", "1") or 1)
    paginator = Paginator(qs, 30)
    page_obj = paginator.get_page(page)

    # для селектов
    kinds = [(k, k) for k, _ in LedgerEntry.Kind.choices]
    sides = [(s, s) for s, _ in LedgerEntry.Side.choices]
    currencies = [("OSP","OSP"),("USDT","USDT")]

    return render(request, "ledger_history.html", {
        "page_obj": page_obj,
        "kinds": kinds, "sides": sides, "currencies": currencies,
        "q": q, "kind": kind, "side": side, "currency": currency,
        "from": date_from, "to": date_to,
    })

@login_required
def refunds_hub(request):
    """
    Единая страница возврата: список моих недавних покупок и форма открытия оспаривания.
    """
    text_orders = (TextProductOrder.objects
        .filter(user=request.user, is_active=True)
        .select_related('product','product__owner')
        .order_by('-purchased_at')[:50])

    art_orders = (ArtworkOrder.objects
        .filter(user=request.user,
                status__in=[ArtworkOrder.Status.COMPLETED,
                            ArtworkOrder.Status.RELEASED,
                            ArtworkOrder.Status.PENDING])
        .select_related('artwork','artwork__owner')
        .order_by('-created_at')[:50])

    # Префилл из GET (?type=text&order=123)
    initial = {'order_type': '', 'order_id': ''} 
    selected = None
    ot = request.GET.get('type')
    oid = request.GET.get('order')
    if ot in {'text','art'} and oid and oid.isdigit():
        initial = {'order_type': ot, 'order_id': int(oid)}
        if ot == 'text':
            order = (TextProductOrder.objects
                     .select_related('product','product__owner')
                     .filter(id=int(oid), user=request.user).first())
            if order:
                product = order.product
                category_val = (product.get_category_display()
                                if hasattr(product, 'get_category_display')
                                else getattr(product, 'category', None))
                selected = {
                    'otype': 'text',
                    'type': 'Текстовый продукт',
                    'order_id': order.id,
                    'product_id': product.id,
                    'price': order.price,
                    'seller': getattr(product.owner, 'username', str(product.owner)),
                    'buyer': getattr(request.user, 'username', str(request.user)),
                    'category': category_val or '—',
                }
        else:
            order = (ArtworkOrder.objects
                     .select_related('artwork','artwork__owner')
                     .filter(id=int(oid), user=request.user).first())
            if order:
                art = order.artwork
                category_val = (art.get_category_display()
                                if hasattr(art, 'get_category_display')
                                else getattr(art, 'category', None))
                selected = {
                    'otype': 'art',
                    'type': 'Артворк',
                    'order_id': order.id,
                    'product_id': art.id,
                    'price': order.price,
                    'seller': getattr(art.owner, 'username', str(art.owner)),
                    'buyer': getattr(request.user, 'username', str(request.user)),
                    'category': category_val or '—',
                }

    form = DisputeOpenForm(initial=initial)
    return render(request, 'refund_hub.html', {   # <-- было 'portfolio.html'
        'form': form,
        'text_orders': text_orders,
        'art_orders': art_orders,
        'selected': selected,
        # если шаблону нужны — можно оставить:
        'tab': 'refunds',
        'sort': request.GET.get('sort', ''),
    })


@login_required
@require_http_methods(["POST"])
@transaction.atomic
def refund_request(request):
    form = DisputeOpenForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Проверьте форму возврата.")
        return redirect('refunds_hub')

    order_type = form.cleaned_data['order_type']
    order_id   = form.cleaned_data['order_id']
    reason     = form.cleaned_data['reason']

    # Находим заказ текущего пользователя и ContentType
    if order_type == 'text':
        order = get_object_or_404(TextProductOrder, pk=order_id, user=request.user)
        order_ct = ContentType.objects.get_for_model(TextProductOrder)
    else:
        order = get_object_or_404(ArtworkOrder, pk=order_id, user=request.user)
        order_ct = ContentType.objects.get_for_model(ArtworkOrder)

    # Эскроу по заказу
    escrow = Escrow.objects.filter(order_ct=order_ct, order_id=order.id).first()
    if not escrow:
        messages.error(request, "Эскроу по этому заказу не найден.")
        return redirect('refunds_hub')

    # Не даём открыть повторно
    if hasattr(escrow, "dispute") and escrow.dispute and escrow.dispute.is_open():
        messages.info(request, "По этому заказу уже есть открытый возврат.")
        return redirect('refunds_hub')

    # Создаём диспут и помечаем escrow
    dispute = Dispute.objects.create(escrow=escrow, opened_by=request.user, reason=reason)

    updates = []
    if not escrow.disputed:
        escrow.disputed = True
        updates.append("disputed")
    if hasattr(escrow, "dispute_reason"):
        escrow.dispute_reason = reason
        updates.append("dispute_reason")
    if hasattr(escrow, "dispute_created_at"):
        from django.utils import timezone
        escrow.dispute_created_at = timezone.now()
        updates.append("dispute_created_at")
    if hasattr(Escrow, "Status") and escrow.status != Escrow.Status.DISPUTED:
        escrow.status = Escrow.Status.DISPUTED
        updates.append("status")
    if updates:
        escrow.save(update_fields=updates)

    messages.success(request, "Заявка на возврат отправлена модератору.")
    return redirect('refund_submitted', pk=dispute.id)



@login_required
def refund_submitted(request, pk: int):
    dispute = get_object_or_404(Dispute, pk=pk, opened_by=request.user)
    esc = dispute.escrow
    order_id = esc.order_id
    order_model = esc.order_ct.model if esc.order_ct else None

    return render(request, 'refund_submitted.html', {
        'dispute': dispute,
        'escrow': esc,
        'order_id': order_id,
        'order_model': order_model,
    })

@login_required
@require_http_methods(["POST"])
@transaction.atomic
def refund_cancel(request, pk: int):

    dispute = get_object_or_404(Dispute, pk=pk, user=request.user, status='open')
    dispute.status = 'canceled'
    dispute.save(update_fields=['status'])

    esc = None
    # Снимем флаг disputed у escrows, если нужно
    try:
        order = dispute.content_object
        esc = getattr(order, 'escrow', None)
        if esc:
            esc.disputed = False
            esc.save(update_fields=['disputed'])
    except Exception:
        pass

    messages.info(request, "Заявка на возврат отменена.")
    return redirect('refunds_hub')