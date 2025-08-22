from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, get_user_model
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.core.mail     import EmailMessage
from django.core.paginator import Paginator
from django.utils import timezone
from django.conf import settings
from django.core.files.base import ContentFile
from django.contrib import messages
import logging, base64, json, imghdr, uuid
from django.db.models import Count, Avg, Q, F, Prefetch, Value
from PIL import Image, ImageDraw, ImageFilter
from django.views.decorators.csrf import csrf_protect
from .crypto.tasks import mint_osp_for_user
from decimal import Decimal
from django.db.models.functions import Coalesce
from django.contrib.auth.decorators import user_passes_test
from django.views.decorators.http import require_POST
from django.utils.http import url_has_allowed_host_and_scheme
from django.shortcuts import redirect, resolve_url



from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from django.http import (
    JsonResponse, 
    HttpResponseRedirect, 
    HttpResponseForbidden, 
    HttpResponseBadRequest
)

from django.urls          import reverse

import hashlib
import random, string, requests
from datetime import timedelta

from .forms import (
    TextProductForm,
    ProfileEditForm,
    CustomUserCreationForm,
    CustomAuthenticationForm,
    LessonForm,
    ArtworkForm,
    ArtworkImageForm,
    ArtworkImageFormSet,
    ArtworkCensorForm,
    TextProductForm,
)

from .models import (
    EmailVerification,
    Purchase,
    PaymentOrder,
    TextProductOrder, 
    CryptoWallet, 
    CryptoTransaction, 
    Currency, 
    ArtworkOrder,
    ArtworkImage,
    CustomUser,
    Visit, Profile,
    Artwork, Lesson, TextProduct,
    TextProductRating,
    Enrollment,
    Notification
)

CustomUser = get_user_model()
logger = logging.getLogger(__name__)


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


def complete_order(request, order_id):
    order = get_object_or_404(ArtworkOrder, pk=order_id, user=request.user)

    try:
        messages.info(
            request,
            "Чтобы завершить заказ, подтвердите транзакцию release() в своём кошельке. "
            "После подтверждения статус обновится автоматически."
        )
        order.onchain_status = "AWAITING_RELEASE"
        order.save(update_fields=["onchain_status"])

        return redirect("order_detail", pk=order.pk)

    except Exception as e:
        logger.error(f"[Escrow Error] Ошибка подготовки к release для заказа {order_id}: {e}")
        messages.error(request, "Не удалось инициировать завершение заказа. Попробуйте ещё раз.")
        return redirect("order_detail", pk=order_id)

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

    if request.method == 'POST':
        code = request.POST.get('code')
        ev = EmailVerification.objects.filter(
            user=user,
            code=code,
            expires_at__gte=timezone.now()
        ).first()

        if ev:
            # Успешно — активируем аккаунт и очищаем
            user.is_active = True
            user.is_email_verified = True
            user.save(update_fields=['is_active', 'is_email_verified'])
            ev.delete()
            login(request, user, backend='users.backends.EmailBackend')
            messages.success(request, "Email подтверждён, вы вошли в систему.")
            return redirect('dashboard')
        else:
            messages.error(request, "Неверный или просроченный код.")

    return render(request, 'registration/verify_email.html')

@anonymous_required
def login_view(request):
    if request.user.is_authenticated and request.GET.get('switch') == '1':
        logout(request)
    elif request.user.is_authenticated and request.method == "GET":
        return redirect('dashboard')

    if request.method == 'POST':
        form = CustomAuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)  # backend возьмётся автоматически
            messages.success(request, f"Вы успешно вошли как {user.username}")
            return redirect('dashboard')
        return render(request, 'registration/login.html', {'form': form}, status=400)
    else:
        form = CustomAuthenticationForm(request=request)
    return render(request, 'registration/login.html', {'form': form})


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
    online_since = timezone.now() - timedelta(minutes=10)
    visits_count = (
        Visit.objects.filter(visited_at__gte=online_since)
        .values('visitor').distinct().count()
    )

    # 7) Сколько уроков создал текущий пользователь
    #    (может подстроить под свою метрику)
    product_count = TextProduct.objects.filter(owner=request.user).count() \
                  + Artwork.objects.filter(owner=request.user).count()
    
    news_items = []

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
        'product_count': product_count,
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
    # передаём пустой контекст,
    # вы будете наполнять шаблон самостоятельно
    return render(request, 'text_products/rules_overview.html')


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
        form = ProfileEditForm(request.POST, request.FILES, instance=request.user)
        if form.is_valid():
            form.save()
            return redirect('edit_profile')
    else:
        form = ProfileEditForm(instance=request.user)

    # Берём актуальное значение популярности
    popularity = profile.popularity

    # Все три вида «товаров» пользователя
    text_products = TextProduct.objects.filter(owner=request.user)
    artworks      = Artwork.objects.filter(owner=request.user)
    lessons       = Lesson.objects.filter(teacher=request.user)

    # Пока пустой список достижений
    achievements = []

    fixed_colors = [
        "#4086f7", "#f05522", "#f0224b", "#752938",
        "#29752e", "#62bd68", "#8abfbb", "#bfb48a",
        "#cc0e11", "#1d172b", "#806a76", "#cadbc8"
    ]

    return render(request, 'profile.html', {
        'form': form,
        'profile': profile,
        'popularity': popularity,
        'text_products': text_products,
        'artworks': artworks,
        'lessons': lessons,
        'achievements': achievements,
        'fixed_colors': fixed_colors,
    })



@login_required
def user_profile_view(request, username):
    visited_user = get_object_or_404(request.user.__class__, username=username)

    # Логика визитов и повышения популярности
    if visited_user != request.user:
        ip = request.META.get('REMOTE_ADDR', '')
        ip_hashed = hash_ip(ip) if ip else ''
        already = Visit.objects.filter(visited=visited_user, visitor=request.user).exists()
        if not already:
            profile, _ = Profile.objects.get_or_create(user=visited_user)
            profile.popularity += 1
            profile.save()
            Visit.objects.create(
                visited=visited_user,
                visitor=request.user,
                ip_address=ip_hashed
            )

    profile, _ = Profile.objects.get_or_create(user=visited_user)
    popularity = profile.popularity

    # Сбор трёх списков для просмотра
    text_products = TextProduct.objects.filter(owner=visited_user)
    artworks      = Artwork.objects.filter(owner=visited_user)
    lessons       = Lesson.objects.filter(teacher=visited_user)

    achievements = []

    return render(request, 'user_profile.html', {
        'visited_user': visited_user,
        'profile': profile,
        'popularity': popularity,
        'text_products': text_products,
        'artworks': artworks,
        'lessons': lessons,
        'achievements': achievements,
    })


@csrf_protect
@login_required
def save_background_color(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        color = data.get('color', None)
        if color:
            profile = getattr(request.user, 'profile', None)
            if profile:
                profile.background_color = color
                profile.save()
                return JsonResponse({'status': 'success'})
            else:
                return JsonResponse({'status': 'error', 'message': 'Профиль не найден'})
        else:
            return JsonResponse({'status': 'error', 'message': 'Цвет не указан'})
    return JsonResponse({'status': 'error', 'message': 'Неверный метод запроса'})

@login_required
def wallets_view(request):
    wallets = request.user.crypto_wallets.prefetch_related(
        Prefetch(
            'txs',
            queryset=CryptoTransaction.objects
                     .filter(tx_type='deposit')
                     .order_by('-created_at'),
            to_attr='deposit_txs'  # эти транзакции будут в списке wallet.deposit_txs
        )
    )
    
    deposit_presets = [5, 10, 25, 50, 100, 500, 1000]

    unread_notifications = request.user.notifications.filter(is_read=False)

    return render(request, 'wallets.html', {
        'wallets': wallets,
        'unread_notifications': unread_notifications,
        'deposit_presets': deposit_presets,    
    })

@login_required
def osp_deposit_instructions(request, amount: int = None):
    wallet = request.user.crypto_wallets.get(currency='OSP')

    # если amount передан в URL, используем его, иначе 0 (тогда пользователь выберет сам)
    deposit_amount = Decimal(amount) if amount else None

    # уникальный reference
    reference = uuid.uuid4().hex
    # создаём запись транзакции с пустым tx_hash и нужной суммой (если есть)
    CryptoTransaction.objects.create(
        wallet=wallet,
        tx_type='deposit',
        amount=deposit_amount or Decimal('0'),
        reference=reference,
        tx_hash=''
    )

    with open(settings.BASE_DIR / 'abi' / 'osp_escrow_abi.json') as f:
        contract_abi = json.load(f)

    return render(request, 'osp_deposit.html', {
        'address':       wallet.address,
        'reference':     reference,
        'token_symbol':  'OSP',
        'network':       'ERC20',
        'deposit_amount': deposit_amount, 
        'escrow_address': settings.OSP_CONTRACT_ADDRESS,
        'contract_abi': json.dumps(contract_abi),   
        'token_decimals': settings.OSP_TOKEN_DECIMALS, 
    })

@login_required
def catalog_text_products(request):
    user        = request.user
    query       = (request.GET.get('q') or '').strip()
    sort_param  = request.GET.get('sort', '-created')   # -created, created, -price, price, -avg_rating, avg_rating
    page        = request.GET.get('page', 1)
    hide_nsfw   = request.GET.get('hide_nsfw', '1')     # по умолчанию скрываем NSFW
    exclude_mine= request.GET.get('exclude_mine', '0')  # по умолчанию показываем и свои

    # База: только одобренные, активные и не soft-deleted
    qs = (
        TextProduct.objects
        .filter(
            status=TextProduct.Status.APPROVED,
            is_active=True,
            is_deleted=False,
        )
        .annotate(avg_rating=Avg('ratings__rating'))
    )

    # Исключить мои товары (опционально)
    if exclude_mine == '1':
        qs = qs.exclude(owner=user)

    # Скрыть NSFW (опционально; по умолчанию включено)
    if hide_nsfw == '1':
        qs = qs.filter(nsfw=False)

    # Поиск
    if query:
        qs = qs.filter(
            Q(title__icontains=query) |
            Q(description__icontains=query) |
            Q(category__icontains=query) |
            Q(keywords__icontains=query)
        )

    # Безопасная сортировка
    allowed_sorts = {'-created', 'created', '-price', 'price', '-avg_rating', 'avg_rating', '-title', 'title'}
    if sort_param not in allowed_sorts:
        sort_param = '-created'

    # Нормализуем поле сортировки
    desc = sort_param.startswith('-')
    key  = sort_param.lstrip('-')


    ORDER_MAP = {
        'created': 'created_at',   # маппинг поля
        'price':   'price',
        'rating':  'avg_rating',   # оставлено для совместимости, если вдруг появится ?sort=rating
        'avg_rating': 'avg_rating',
        'title': 'title',
    }
    field = ORDER_MAP.get(key, 'created_at')

    # Сортируем: для avg_rating учитываем NULLS LAST
    if field == 'avg_rating':
        expr = F('avg_rating').desc(nulls_last=True) if desc else F('avg_rating').asc(nulls_last=True)
        qs = qs.order_by(expr, '-created_at')  # стабильная вторая сортировка
    else:
        prefix = '-' if desc else ''
        qs = qs.order_by(prefix + field, '-created_at')

    # Пагинация
    paginator = Paginator(qs, 24)  # 24 карточки на страницу
    page_obj = paginator.get_page(page)

    context = {
        'query': query,
        'sort': sort_param,
        'hide_nsfw': hide_nsfw,
        'exclude_mine': exclude_mine,
        'page_obj': page_obj,
        'products': page_obj.object_list,  # если шаблон ожидает products
    }
    return render(request, 'catalog_text_products.html', context)


@login_required
def add_text_product(request):
    if request.method == 'POST':
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
                messages.success(request, "Черновик сохранён — вы можете закончить его позже.")
                return HttpResponseRedirect(reverse('portfolio') + '#drafts')
            return render(request, 'add_text_product.html', {'form': form}, status=400)

        if 'submit_review' in request.POST:
            if form.is_valid():
                prod = form.save(owner=request.user)
                prod.submit_for_review()
                messages.success(request, "Продукт отправлен на проверку.")
                return HttpResponseRedirect(reverse('portfolio') + '#pending')
            return render(request, 'add_text_product.html', {'form': form}, status=400)

        # Нажали что-то иное / отсутствует имя кнопки — просто показать ошибки
        return render(request, 'add_text_product.html', {'form': form}, status=400)

    # GET
    form = TextProductForm()
    return render(request, 'add_text_product.html', {'form': form})


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

    decrypted_content = product.decrypt_content() if can_view_content else None
    keywords = [tag.strip() for tag in product.keywords.split(',')] if product.keywords else []

    context = {
        'product': product,
        'can_view_content': can_view_content,
        'decrypted_content': decrypted_content,
        'keywords': keywords,
    }
    return render(request, 'view_text_product.html', context)

@login_required
def public_view_text_product(request, product_id):
    product = get_object_or_404(TextProduct, pk=product_id)

    # владелец — в своё приватное вью (если нужно), иначе проверяем покупку
    if product.owner_id == request.user.id:
        return redirect('owner_text_product', pk=product.id)

    has_purchased = TextProductOrder.objects.filter(
        user=request.user, product=product, is_active=True
    ).exists()

    can_view_content = request.user.is_staff or has_purchased
    decrypted_content = product.decrypt_content() if can_view_content else None

    avg_rating = product.ratings.aggregate(avg=Avg('rating'))['avg']  # <-- rating, не score
    keywords = [k.strip() for k in (product.keywords or '').split(',') if k.strip()]

    return render(request, 'view_text_product.html', {
        'product': product,
        'can_view_content': can_view_content,
        'decrypted_content': decrypted_content,
        'avg_rating': avg_rating,
        'keywords': keywords,
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
def buy_text_product(request, product_id):
    product = get_object_or_404(
        TextProduct,
        pk=product_id,
        status=TextProduct.Status.APPROVED,
        is_active=True,
        is_deleted=False,
    )

    if product.owner_id == request.user.id:
        messages.info(request, "Это ваш продукт — покупать не нужно.")
        return redirect('public_view_text_product', product_id=product.id)

    if TextProductOrder.objects.filter(user=request.user, product=product, is_active=True).exists():
        messages.info(request, "Вы уже купили этот продукт.")
        return redirect('public_view_text_product', product_id=product.id)

    # Пока поддержим покупки за OSP. Для USDT — отдельная реализация позже.
    if product.currency != Currency.OSP:
        messages.warning(request, "Покупка этого типа за USDT будет доступна позже. Сейчас доступна покупка за OSP.")
        return redirect('public_view_text_product', product_id=product.id)

    wallet = CryptoWallet.objects.filter(user=request.user, currency=Currency.OSP).first()
    if not wallet:
        messages.error(request, "OSP-кошелёк не найден. Обратитесь в поддержку.")
        return redirect('public_view_text_product', product_id=product.id)

    if wallet.balance < product.price:
        messages.error(request, "Недостаточно средств в кошельке OSP.")
        return redirect('public_view_text_product', product_id=product.id)

    # Фиксируем покупку
    TextProductOrder.objects.create(
        user=request.user, product=product, price=product.price, is_active=True
    )

    # Списываем OSP (фоновая задача) + логируем транзакцию
    try:
        mint_osp_for_user.delay(request.user.id, -int(product.price))  # как в покупках артов
    except Exception:
        pass
    CryptoTransaction.objects.create(
        wallet=wallet, tx_type='purchase',
        amount=Decimal(product.price) * Decimal('-1'),
        reference=f"text:{product.id}"
    )

    messages.success(request, "Покупка успешно оформлена. Доступ к контенту открыт.")
    return redirect('public_view_text_product', product_id=product.id)


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
    if request.method == 'POST':
        try:
            rating_val = int(request.POST.get('rating'))
        except (ValueError, TypeError):
            messages.error(request, "Некорректная оценка.")
            return redirect('view_text_product', product_id=product.id)
        if 1 <= rating_val <= 5:
            rating_obj, created = TextProductRating.objects.update_or_create(
                product=product, user=request.user,
                defaults={'rating': rating_val}
            )
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
    if request.method == 'POST':
        form = ArtworkForm(request.POST)
        if form.is_valid():
            art = form.save(commit=False)
            art.owner = request.user
            art.save()
            return redirect('artwork_pages', pk=art.pk)
    else:
        form = ArtworkForm()
    return render(request, 'artwork/artwork_create.html', {'form': form})


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
    })


@login_required
def create_artwork_page(request, pk):
    """
    AJAX-эндпоинт: POST с файлом 'image', создаём новую страницу.
    """
    if request.method != 'POST' or 'image' not in request.FILES:
        return HttpResponseBadRequest("Неверный запрос")

    artwork = get_object_or_404(Artwork, pk=pk, owner=request.user)
    if artwork.pages.count() >= 10:
        return JsonResponse({
            'success': False,
            'errors': 'Достигнут лимит: не более 10 страниц.'
        }, status=400)
    
    
    next_order = artwork.pages.count()

    uploaded_file = request.FILES['image']
    if imghdr.what(uploaded_file) not in ['jpeg', 'png', 'jpg']:
        return JsonResponse({'success': False, 'errors': 'Недопустимый формат изображения.'})

    new_page = ArtworkImage(
        artwork=artwork,
        order=next_order,
        image=request.FILES['image']
    )
    new_page.save()
    return JsonResponse({
        'success': True,
        'page_id': new_page.id,
        'url':     new_page.image.url
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
    return render(request, 'artwork/artwork_detail.html', {
        'artwork': artwork,
        'pages':    pages,
    })


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

@login_required
def artwork_store(request):
    q        = request.GET.get('q', '')
    category = request.GET.get('category', '')
    sort     = request.GET.get('sort', 'popularity')

    # Базовый queryset: только одобренные работы
    qs = Artwork.objects.filter(is_approved=True).prefetch_related('pages')

    if q:
        qs = qs.filter(title__icontains=q)

    if category:
        qs = qs.filter(category=category)

    # Аннотация: сколько раз каждый artwork был куплен
    qs = qs.annotate(order_count=Count('orders'))

    # Сортировки
    if sort == 'price_asc':
        qs = qs.order_by('price')
    elif sort == 'price_desc':
        qs = qs.order_by('-price')
    elif sort == 'newest':
        qs = qs.order_by('-created_at')
    else:  # popularity
        qs = qs.order_by('-order_count')

    # Пагинация
    paginator = Paginator(qs, 12)  # 12 работ на страницу
    page_obj  = paginator.get_page(request.GET.get('page'))

    # Список купленных артворков текущим юзером
    if request.user.is_authenticated:
        purchased_ids = set(
            ArtworkOrder.objects
            .filter(user=request.user)
            .values_list('artwork_id', flat=True)
        )
    else:
        purchased_ids = set()

    return render(request, 'artwork_store.html', {
        'q': q,
        'category': category,
        'sort': sort,
        'categories': Artwork.OWNER_CHOICES,
        'page_obj': page_obj,
        'purchased_ids': purchased_ids,
    })

@login_required
def view_artwork_public(request, artwork_id):
    artwork = get_object_or_404(Artwork, pk=artwork_id)

    if artwork.owner == request.user:
        return redirect('view_artwork_private', artwork_id=artwork_id)

    has_access = ArtworkOrder.objects.filter(
        user=request.user,
        artwork=artwork,
        status__in=[
            ArtworkOrder.Status.COMPLETED,
            ArtworkOrder.Status.RELEASED,
        ]
    ).exists()

    pages = artwork.pages.order_by('order') if has_access else []

    return render(request, 'artwork_detail_public.html', {
        'artwork': artwork,
        'has_access': has_access,
        'pages': pages
    })

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
def buy_artwork(request, artwork_id):
    user = request.user

    # 1) Получаем артоворк, проверяем, что он одобрен, активен и есть копии
    art = get_object_or_404(
        Artwork,
        pk=artwork_id,
        is_approved=True,
        is_active=True,
        available_copies__gt=0
    )

    # 2) Ветка для внутренней валюты OSP
    if art.currency == Currency.OSP:
        # Проверка повторной покупки
        if ArtworkOrder.objects.filter(user=user, artwork=art).exists():
            messages.info(request, "Вы уже купили этот артворк.")
            return redirect('view_artwork_public', artwork_id=art.id)

        # Проверяем кошелёк и баланс
        wallet = CryptoWallet.objects.filter(user=user, currency=Currency.OSP).first()
        if not wallet:
            return HttpResponseForbidden("Кошелек не найден")
        if wallet.balance < art.price:
            return HttpResponseForbidden("Недостаточно средств")

        # Списываем монеты и создаём заказ
        order = ArtworkOrder.objects.create(
            user=user, 
            artwork=art, 
            tx_hash='',
            amount=art.price
            )
        mint_osp_for_user.delay(user.id, -int(art.price))

        # Уменьшаем тираж и, если нужно, снимаем с продажи
        art.available_copies -= 1
        if art.available_copies <= 0:
            art.is_active = False
        art.save(update_fields=['available_copies', 'is_active'])

        messages.success(request, "Покупка успешно совершена — монеты списаны и средства ушли в эскроу.")
        return redirect('purchase_success', order_id=order.id)

    # 3) Ветка для on-chain оплаты USDT
    elif art.currency == Currency.USDT:
        # Если уже полностью оплачен ранее
        if PaymentOrder.objects.filter(user=user, artwork=art, status='paid').exists():
            messages.info(request, "Вы уже оплатили этот артворк.")
            return redirect('view_artwork_public', artwork_id=art.id)

        # Если есть незавершённый (pending) заказ — показываем инструкции к нему
        pending = PaymentOrder.objects.filter(user=user, artwork=art, status='pending').first()
        if pending:
            return render(request, 'payment_instructions.html', {
                'order': pending,
                'escrow_address': settings.ESCROW_CONTRACT_ADDRESS,
            })

        # Создаём новый on-chain заказ
        po = PaymentOrder.objects.create(
            user=user,
            artwork=art,
            amount=art.price,
            currency=Currency.USDT,
            status='pending'
        )
        return render(request, 'payment_instructions.html', {
            'order': po,
            'escrow_address': settings.ESCROW_CONTRACT_ADDRESS,
        })

    # 4) Если валюта не поддерживается
    else:
        return HttpResponseForbidden("Валюта не поддерживается")

@login_required
def purchase_success(request, order_id):
    order = get_object_or_404(ArtworkOrder, pk=order_id)
    return render(request, 'purchase_success.html', {'order': order})

@login_required
def delete_artwork(request, pk):
    art = get_object_or_404(Artwork, pk=pk, owner=request.user)
    if art.status == Artwork.Status.PENDING:
        messages.warning(request, 'Нельзя удалить артворк на модерации.')
    else:
        art.delete()
        messages.success(request, 'Артворк удалён.')
    return redirect('portfolio')

@login_required
def pause_artwork(request, pk):
    art = get_object_or_404(Artwork, pk=pk, owner=request.user, status=Artwork.Status.APPROVED)
    art.is_active = False
    art.save(update_fields=['is_active'])
    messages.info(request, 'Продажа приостановлена.')
    return redirect('portfolio')

@login_required
def resume_artwork(request, pk):
    art = get_object_or_404(Artwork, pk=pk, owner=request.user, status=Artwork.Status.APPROVED)
    art.is_active = True
    art.save(update_fields=['is_active'])
    messages.success(request, 'Продажа возобновлена.')
    return redirect('portfolio')


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


@login_required
def portfolio(request):
    tab = request.GET.get('tab', 'drafts')  # по умолчанию — черновики
    user = request.user

    # --- ТЕКСТЫ: исключаем мягко удалённые владельцем ---
    tp = TextProduct.objects.filter(owner=user, is_deleted=False)

    # Черновики
    drafts_texts = tp.filter(status=TextProduct.Status.DRAFT)
    # На рассмотрении
    pending_texts = tp.filter(status=TextProduct.Status.PENDING)
    # Отклонённые
    rejected_texts = tp.filter(status=TextProduct.Status.REJECTED)
    # В продаже: показываем все одобренные (и активные, и приостановленные),
    # чтобы можно было "продолжить продажу"
    on_sale_texts = tp.filter(status=TextProduct.Status.APPROVED)

    # --- АРТЫ: как было (если у артов нет soft-delete — ничего не меняем) ---
    drafts_art = Artwork.objects.filter(owner=user, status=Artwork.Status.DRAFT)
    pending_art = Artwork.objects.filter(owner=user, status=Artwork.Status.PENDING)
    rejected_art = Artwork.objects.filter(owner=user, status=Artwork.Status.REJECTED)
    # Раньше у тебя здесь было только is_active=True; оставим как было для артов
    on_sale_art = Artwork.objects.filter(
        owner=user,
        status=Artwork.Status.APPROVED,
        is_active=True
    )

    # Объединяем списки (как у тебя было)
    drafts   = list(drafts_texts)   + list(drafts_art)
    pending  = list(pending_texts)  + list(pending_art)
    rejected = list(rejected_texts) + list(rejected_art)
    # Для вкладки "В продаже" тексты: и активные, и приостановленные; арты — только активные
    on_sale  = list(on_sale_texts)  + list(on_sale_art)

    # --- Приобретённые: НЕ фильтруем по is_deleted, чтобы покупки оставались у пользователей ---
    purchased_texts    = TextProduct.objects.filter(orders__user=user).distinct()
    purchased_artworks = Artwork.objects.filter(orders__user=user).distinct()

    # Остальное — по желанию; если используешь где-то в шаблоне — оставляем
    purchased_lessons = Enrollment.objects.filter(user=user, paid=True).select_related('lesson')
    own_texts    = tp.filter(status=TextProduct.Status.APPROVED, is_active=True)
    own_lessons  = Lesson.objects.filter(teacher=user)
    own_artworks = Artwork.objects.filter(owner=user, status=Artwork.Status.APPROVED, is_active=True)
    user_artworks = Artwork.objects.filter(owner=user).order_by('-created')

    context = {
        'tab': tab,
        'drafts': drafts,
        'pending': pending,
        'rejected': rejected,
        'on_sale': on_sale,
        'purchased_texts': purchased_texts,
        'purchased_artworks': purchased_artworks,
        'purchased_lessons': purchased_lessons,
        'own_texts': own_texts,
        'own_lessons': own_lessons,
        'own_artworks': own_artworks,
        'drafts_count': len(drafts),
        'pending_count': len(pending),
        'rejected_count': len(rejected),
        'on_sale_count': len(on_sale),
    }
    return render(request, 'portfolio.html', context)



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
