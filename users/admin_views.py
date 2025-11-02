
from django.shortcuts import render, get_object_or_404, redirect
from django.core.paginator import Paginator
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.contrib import messages
from django.urls import reverse
from django import forms
from decimal import Decimal
from uuid import uuid4
from .accounting import book_osp, decimal_to_micros
from datetime import timedelta
from .money import settle_sale_with_platform_fee_osp, micros_to_usd
from django.db.models import Q

from django.http import JsonResponse, HttpResponseBadRequest
from django.db import transaction
from .crypto.web3 import build_release_tx, build_refund_tx
from django.conf import settings
from functools import wraps
from django.core.exceptions import PermissionDenied
from cryptography.fernet import InvalidToken

from .utils import(
    decrypt_text, account, 
    send_tx, kill_user_sessions,
    grant_vip_gift, revoke_vip,
    )

from .models import (
    TextProduct, TextProductOrder, 
    ArtworkOrder, Artwork, 
    Notification, CustomUser, 
    RefundRequest, Escrow,
    CryptoWallet, Currency,
    BanRecord, News, VIPPlan,
    CryptoTransaction, 
    LedgerEntry, VIPSubscription,
    VIPGiftRecord,
    )



def _is_staff_or_superuser(u):
    return bool(u and u.is_authenticated and (u.is_staff or u.is_superuser))

def staff_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        u = request.user
        if not u.is_authenticated:
            # безопасно уводим на страницу логина
            from django.conf import settings
            from django.shortcuts import redirect
            return redirect(getattr(settings, "LOGIN_URL", "/login/"))
        if not _is_staff_or_superuser(u):
            # чёткий отказ без редирект-петель
            raise PermissionDenied("Admin area only")
        return view_func(request, *args, **kwargs)
    return _wrapped


@staff_required
def admin_index(request):
    return render(request, 'admin/admin_index.html')


class NewsForm(forms.ModelForm):
    class Meta:
        model = News
        fields = ("title", "content", "published_at", "is_published")
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control", "placeholder": "Заголовок"}),
            "content": forms.Textarea(attrs={"class": "form-control", "rows": 10, "placeholder": "Текст новости"}),
            "published_at": forms.DateTimeInput(attrs={"class": "form-control", "type": "datetime-local"}),
            "is_published": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def clean(self):
        cleaned = super().clean()
        title = (cleaned.get("title") or "").strip()
        content = (cleaned.get("content") or "").strip()
        is_published = cleaned.get("is_published")
        published_at = cleaned.get("published_at")

        cleaned["title"] = title
        cleaned["content"] = content

        # если отмечено «опубликована», но дата не задана — ставим сейчас
        if is_published and not published_at:
            cleaned["published_at"] = timezone.now()

        # datetime-local приходит как naive — делаем aware в текущем TZ
        if published_at and timezone.is_naive(published_at):
            cleaned["published_at"] = timezone.make_aware(published_at, timezone.get_current_timezone())

        return cleaned


@staff_required
def news_list(request):
    """
    Фильтры:
      ?q=...                          поиск по заголовку/контенту
      ?status=all|published|draft|scheduled
      ?page=N
    """
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "all").strip()
    now = timezone.now()

    qs = News.objects.all()

    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(content__icontains=q))

    if status == "published":
        qs = qs.filter(is_published=True, published_at__lte=now)
    elif status == "draft":
        qs = qs.filter(is_published=False)
    elif status == "scheduled":
        qs = qs.filter(is_published=True, published_at__gt=now)
    # else: all

    qs = qs.order_by("-published_at", "-id")

    page = int(request.GET.get("page") or 1)
    page_obj = Paginator(qs, 20).get_page(page)

    return render(request, "admin/news_list.html", {
        "page_obj": page_obj,
        "q": q,
        "status": status,
        "now": now,  # понадобится в шаблоне для бейджа «Запланирована»
    })


@staff_required
def news_create(request):
    if request.method == "POST":
        form = NewsForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.author = request.user  # фикс: автор из текущего пользователя
            obj.save()
            messages.success(request, "Новость сохранена.")
            return redirect("users_admin:news_list")
    else:
        # дефолт — черновик, дата = сейчас (можно не трогать)
        form = NewsForm(initial={"published_at": timezone.now(), "is_published": False})
    return render(request, "admin/news_form.html", {"form": form})

@staff_required
def news_edit(request, pk):
    obj = get_object_or_404(News, pk=pk)
    if request.method == "POST":
        form = NewsForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, "Изменения сохранены.")
            return redirect("users_admin:news_list")
    else:
        form = NewsForm(instance=obj)
    return render(request, "admin/news_form.html", {"form": form, "object": obj})


@staff_required
def news_delete(request, pk):
    obj = get_object_or_404(News, pk=pk)
    if request.method == "POST":
        obj.delete()
        messages.success(request, "Новость удалена.")
        return redirect("users_admin:news_list")
    return render(request, "admin/news_confirm_delete.html", {"object": obj})


# Быстрые действия: «Опубликовать сейчас» / «Снять с публикации»
@staff_required
@require_POST
def news_publish_now(request, pk):
    obj = get_object_or_404(News, pk=pk)
    obj.is_published = True
    obj.published_at = timezone.now()
    obj.save(update_fields=["is_published", "published_at"])
    messages.success(request, "Новость опубликована.")
    return redirect("users_admin:news_list")


@staff_required
@require_POST
def news_unpublish(request, pk):
    obj = get_object_or_404(News, pk=pk)
    obj.is_published = False
    obj.save(update_fields=["is_published"])
    messages.success(request, "Новость переведена в черновик.")
    return redirect("users_admin:news_list")



@staff_required
class GrantOSPForm(forms.Form):
    # ищем по username или email
    query = forms.CharField(
        label="Пользователь (username или email)",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "username или email"}),
    )
    amount = forms.DecimalField(
        label="Сумма OSP",
        min_value=Decimal("0.01"),
        max_digits=18, decimal_places=6,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min":"0.01"}),
        help_text="Минимум 0.01 OSP. Внутренний учёт ведётся в микросах."
    )
    note = forms.CharField(
        label="Комментарий (необязательно)",
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "за что награда / тест транзакции"}),
    )

    def clean_query(self):
        q = (self.cleaned_data["query"] or "").strip()
        try:
            if "@" in q:
                user = CustomUser.objects.get(email__iexact=q)
            else:
                user = CustomUser.objects.get(username__iexact=q)
        except CustomUser.DoesNotExist:
            raise forms.ValidationError("Пользователь не найден.")
        return user

@staff_required
def admin_grant_osp(request):
    if request.method == "POST":
        form = GrantOSPForm(request.POST)
        if form.is_valid():
            user  = form.cleaned_data["query"]
            amt   = form.cleaned_data["amount"].quantize(Decimal("0.000001"))
            note  = form.cleaned_data.get("note") or ""

            # кошелёк OSP (создаётся автоматически сигналом post_save)
            wallet = CryptoWallet.objects.get(user=user, currency=Currency.OSP)

            ref = f"admin:grant:{uuid4().hex}"
            micros = decimal_to_micros(amt)

            with transaction.atomic():
                # 1) бухгалтерская запись + изменение баланса
                entry = book_osp(wallet, kind=LedgerEntry.Kind.TOPUP_OSP_SOFT, reference=ref, delta_micros=micros)

                # 2) «человеческая» запись в истории кошелька
                CryptoTransaction.objects.create(
                    wallet=wallet, tx_type="deposit",
                    amount=amt, amount_micros=micros,
                    reference=f"{ref}::{note}"[:100],
                    tx_hash=""  # для on-chain можно проставить позже
                )

            messages.success(request, f"Начислено {amt} OSP пользователю @{user.username}.")
            # Можно показать короткую «квитанцию»
            request.session["grant_osp_receipt"] = {
                "user": user.username,
                "amount": str(amt),
                "reference": ref,
                "balance_after": str(wallet.balance),  # уже обновлён в book_osp
                "ts": timezone.now().isoformat(timespec="seconds"),
            }
            return redirect("users_admin:grant_osp_success")
    else:
        form = GrantOSPForm()

    return render(request, "admin/grant_osp.html", {"form": form})

@staff_required
def admin_grant_osp_success(request):
    receipt = request.session.pop("grant_osp_receipt", None)
    return render(request, "admin/grant_osp_success.html", {"receipt": receipt})



@staff_required
def review_text_products(request):
    q       = (request.GET.get("q") or "").strip()
    owner   = (request.GET.get("owner") or "").strip()
    cur     = (request.GET.get("currency") or "").strip()

    status_param = (request.GET.get("status") or "pending").strip().lower()

    status_map = {
        "pending":  TextProduct.Status.PENDING,
        "approved": TextProduct.Status.APPROVED,
        "rejected": TextProduct.Status.REJECTED,
        "all": None,   
    }
    target_status = status_map.get(status_param, TextProduct.Status.PENDING)

    qs = TextProduct.objects.select_related("owner")

    if q:
        qs = qs.filter(title__icontains=q) | qs.filter(description__icontains=q)

    if owner:
        qs = qs.filter(owner__username__icontains=owner)

    if cur:
        qs = qs.filter(currency=cur)

    # статусный фильтр
    if target_status is not None:
        qs = qs.filter(status=target_status)

    qs = qs.order_by("-created_at")

    # пагинация как у тебя (оставь свою)
    page = int(request.GET.get("page") or 1)
    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(page)

    return render(request, "admin/review_text_products.html", {
        "items": page_obj.object_list,
        "page_obj": page_obj,
        "q": q,
        "owner": owner,
        "currency": cur,
        "status": status_param,  # сохраняем исходное значение в шаблон
    })

@staff_required
def text_product_detail(request, product_id):
    p = get_object_or_404(TextProduct, pk=product_id)
    open_escrows = _collect_open_escrows_for_product(p)
    product = get_object_or_404(TextProduct, pk=product_id)
    full_content = ""
    try:
        full_content = decrypt_text(product.encrypted_content)
    except InvalidToken:
        messages.error(request, "Не удалось расшифровать контент. Обратитесь к администратору: возможно нужен ре-шифр.")

    return render(request, 'admin/text_product_detail.html', {
        'product': product,
        'full_content': full_content,
        "p": p,
        "open_escrows": open_escrows,
    })


@staff_required
@require_POST
def approve_text_product(request, product_id):
    product = TextProduct.objects.filter(id=product_id, status=TextProduct.Status.PENDING).first()
    if not product:
        messages.info(request, "Эта заявка уже обработана.")
        return redirect('users_admin:review_text_products')

    try:
        quality     = int(request.POST.get("quality", 0))
        uniqueness  = int(request.POST.get("uniqueness", 0))
        spelling    = int(request.POST.get("spelling", 0))
    except ValueError:
        messages.error(request, "Введите корректные числовые значения (0..100).")
        return redirect('users_admin:text_product_detail', product_id=product_id)

    if not (0 <= quality <= 100 and 0 <= uniqueness <= 100 and 0 <= spelling <= 100):
        messages.error(request, "Значения должны быть от 0 до 100.")
        return redirect('users_admin:text_product_detail', product_id=product_id)

    product.quality    = quality
    product.uniqueness = uniqueness
    product.spelling   = spelling
    # Если есть метод:
    if hasattr(product, "mark_approved"):
        product.mark_approved()
    else:
        product.status = TextProduct.Status.APPROVED

    product.save()

    messages.success(request, "Продукт одобрен и опубликован!")
    return redirect(f"{reverse('users_admin:review_text_products')}?status=pending")


@staff_required
def reject_text_product(request, product_id):
    product = TextProduct.objects.filter(id=product_id).first()
    if not product:
        messages.info(request, "Заявка не найдена.")
        return redirect('users_admin:review_text_products')

    if request.method == 'POST':
        if product.status != TextProduct.Status.PENDING:
            messages.info(request, "Эта заявка уже обработана.")
            return redirect('users_admin:review_text_products')

        title = product.title
        if hasattr(product, "mark_rejected"):
            product.mark_rejected(reason="Отклонено администратором")
        else:
            product.status = TextProduct.Status.REJECTED
            product.rejection_reason = "Отклонено администратором"
            product.save(update_fields=['status', 'rejection_reason'])

        messages.warning(request, f'Продукт «{title}» отклонён.')
        return redirect(f"{reverse('users_admin:review_text_products')}?status=pending")

    return redirect(f"{reverse('users_admin:review_text_products')}?status=pending")


@staff_required
def review_artworks(request):
    q       = (request.GET.get("q") or "").strip()
    owner   = (request.GET.get("owner") or "").strip()
    cur     = (request.GET.get("currency") or "").strip()
    status_param = (request.GET.get("status") or "pending").strip().lower()

    status_map = {
        "pending":  Artwork.Status.PENDING,
        "approved": Artwork.Status.APPROVED,
        "rejected": Artwork.Status.REJECTED,
        "all": None,
    }
    target_status = status_map.get(status_param, Artwork.Status.PENDING)

    qs = Artwork.objects.select_related("owner")

    if q:
        qs = qs.filter(title__icontains=q) | qs.filter(description__icontains=q)

    if owner:
        qs = qs.filter(owner__username__icontains=owner)

    if cur:
        qs = qs.filter(currency=cur)

    if target_status is not None:
        qs = qs.filter(status=target_status)

    qs = qs.order_by("-created_at")

    page = int(request.GET.get("page") or 1)
    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(page)

    return render(request, "admin/review_artworks.html", {
        "items": page_obj.object_list,
        "page_obj": page_obj,
        "q": q,
        "owner": owner,
        "currency": cur,
        "status": status_param,
    })



@staff_required
def artwork_detail(request, art_id):
    a = get_object_or_404(Artwork, pk=art_id)
    open_escrows = _collect_open_escrows_for_artwork(a)
    art = get_object_or_404(Artwork, pk=art_id)
    return render(request, 'admin/artwork_detail.html', {
        'art': art,
        "a": a,
        "open_escrows": open_escrows,
        })


@staff_required
@require_POST
def approve_artwork(request, art_id):
    art = Artwork.objects.filter(id=art_id, status=Artwork.Status.PENDING).first()
    if not art:
        messages.info(request, "Эта заявка уже обработана.")
        return redirect('users_admin:review_artworks')  # вернёмся на список

    art.status      = Artwork.Status.APPROVED
    art.is_approved = True
    art.approved_at = timezone.now()
    art.save(update_fields=['status', 'is_approved', 'approved_at'])

    messages.success(request, f'Артворк «{art.title}» одобрен.')
    # фильтр на pending, чтобы запись исчезла
    return redirect(f"{reverse('users_admin:review_artworks')}?status=pending")


@staff_required
def reject_artwork(request, art_id):
    # допускаем GET для показа формы, а вот применять — только POST
    art = Artwork.objects.filter(id=art_id).first()
    if not art:
        messages.info(request, "Заявка не найдена.")
        return redirect('users_admin:review_artworks')

    if request.method == 'POST':
        # разрешаем отклонять только pending; если уже не pending — сообщаем
        if art.status != Artwork.Status.PENDING:
            messages.info(request, "Эта заявка уже обработана.")
            return redirect('users_admin:review_artworks')

        reason = request.POST.get('reason', '').strip()
        if not reason:
            messages.error(request, "Укажите причину отклонения.")
            return redirect('users_admin:reject_artwork', art_id=art.id)

        art.status           = Artwork.Status.REJECTED
        art.is_approved      = False
        art.rejection_reason = reason
        art.save(update_fields=['status', 'is_approved', 'rejection_reason'])

        Notification.objects.create(
           user=art.owner,
           message=f'Ваш артворк «{art.title}» отклонён администратором: «{reason}»',
           link=reverse('portfolio') + '?tab=rejected'
        )

        messages.warning(request, f'Артворк «{art.title}» отклонён.')
        return redirect(f"{reverse('users_admin:review_artworks')}?status=pending")

    # GET: показываем страницу подтверждения
    return redirect(f"{reverse('users_admin:review_artworks')}?status=pending")

@staff_required
def user_search(request):
    q = request.GET.get('q', '').strip()
    if q:
        users = CustomUser.objects.filter(username__icontains=q) \
               | CustomUser.objects.filter(email__icontains=q)
    else:
        users = CustomUser.objects.all()
    return render(request, 'admin/user_search.html', {
        'users': users,
        'q':     q,
    })

@staff_required
def user_detail(request, user_id):
    user_obj       = get_object_or_404(CustomUser, pk=user_id)
    vip_plans = VIPPlan.objects.all().order_by('id')
    text_products  = TextProduct.objects.filter(owner=user_obj)
    artworks       = Artwork.objects.filter(owner=user_obj)

    user_created_at = getattr(user_obj, 'date_joined', None) or getattr(user_obj, 'created_at', None)

    current_sub = (VIPSubscription.objects
                   .select_related('plan')
                   .filter(user=user_obj, end_at__gt=timezone.now())
                   .order_by('-end_at')
                   .first())
    current_vip_plan = current_sub.plan if current_sub else None
    current_vip_until = current_sub.end_at if current_sub else None

    vip_quick = [
        (24,  "1 день"),
        (72,  "3 дня"),
        (168, "7 дней"),
        (720, "30 дней"),
    ]

    return render(request, 'admin/user_detail.html', {
        'user_obj':      user_obj,
        'vip_plans':     vip_plans,
        'text_products': text_products,
        'artworks':      artworks,
        'vip_quick':     vip_quick,
        'user_created_at':   user_created_at,
        'current_vip_plan':  current_vip_plan,
        'current_vip_until': current_vip_until,
    })

@staff_required
def ban_user(request, user_id):
    u = get_object_or_404(CustomUser, pk=user_id)
    if request.method != "POST":
        return redirect('users_admin:user_detail', user_id=user_id)

    hours  = int(request.POST.get("hours", "0") or "0")
    reason = (request.POST.get("reason") or "").strip()

    until = None
    if hours > 0:
        until = timezone.now() + timezone.timedelta(hours=hours)

    u.is_banned  = True
    u.ban_reason = reason or f"Блокировка на {hours} ч." if hours else "Блокировка (без срока)"
    u.ban_until  = until
    u.save(update_fields=["is_banned","ban_reason","ban_until"])

    # аудит + убийство сессий
    BanRecord.objects.create(user=u, action="BAN", reason=u.ban_reason, until=until, moderator=request.user)
    kill_user_sessions(u.id)

    messages.success(request, f'Пользователь «{u.username}» забанен{f" на {hours} ч." if hours else ""}.')
    return redirect('users_admin:user_detail', user_id=user_id)

@staff_required
def unban_user(request, user_id):
    u = get_object_or_404(CustomUser, pk=user_id)
    u.is_banned  = False
    u.ban_reason = ""
    u.ban_until  = None
    u.save(update_fields=["is_banned","ban_reason","ban_until"])
    BanRecord.objects.create(user=u, action="UNBAN", moderator=request.user)

    messages.success(request, f'Пользователь «{u.username}» разбанен.')
    return redirect('users_admin:user_detail', user_id=user_id)

@staff_required
def grant_vip(request, user_id):
    if request.method != "POST":
        return redirect('users_admin:user_detail', user_id=user_id)

    u = get_object_or_404(CustomUser, pk=user_id)
    plan_id = request.POST.get("plan_id")
    hours   = request.POST.get("hours")
    reason  = (request.POST.get("reason") or "").strip()

    try:
        plan = VIPPlan.objects.get(pk=plan_id)
    except VIPPlan.DoesNotExist:
        messages.error(request, "Не выбран корректный VIP-план.")
        return redirect('users_admin:user_detail', user_id=user_id)

    hours_int = int(hours) if (hours and hours.strip().isdigit()) else 0

    with transaction.atomic():
        now = timezone.now()
        if hours_int > 0:
            start = now
            end   = now + timedelta(hours=hours_int)
            VIPSubscription.objects.create(user=u, plan=plan, start_at=start, end_at=end)
            VIPGiftRecord.objects.create(
                user=u, plan=plan, reason=reason, starts_at=start, ends_at=end, moderator=request.user
            )
            tail = f" на {hours_int} ч."
        else:
            sub = VIPSubscription.grant_or_extend(u, plan)
            VIPGiftRecord.objects.create(
                user=u, plan=plan, reason=reason, starts_at=sub.start_at, ends_at=sub.end_at, moderator=request.user
            )
            tail = f" на {plan.duration_days} дн."

    messages.success(request, f"VIP {plan.title} выдан пользователю «{u.username}»{tail}.")
    return redirect('users_admin:user_detail', user_id=user_id)

@staff_required
def revoke_vip(request, user_id):
    u = get_object_or_404(CustomUser, pk=user_id)
    revoke_vip(u, reason="Административное снятие VIP", moderator=request.user)
    messages.success(request, f"VIP снят у пользователя «{u.username}».")
    return redirect('users_admin:user_detail', user_id=user_id)


@staff_required
def delete_user_text_product(request, user_id, product_id):
    product = get_object_or_404(TextProduct, pk=product_id, owner_id=user_id)

    # Расшифровываем содержимое для предпросмотра
    try:
        full_content = decrypt_text(product.encrypted_content, product.owner)
    except Exception:
        full_content = product.encrypted_content
    if request.method == 'POST':
        product.delete()
        messages.success(request, 'Текстовый продукт удалён')
        return redirect('users_admin:user_detail', user_id=user_id)

    # Передаём user_id, чтобы шаблон корректно строил ссылку «Отмена»
    return render(request, 'admin/confirm_delete_user_text_product.html', {
        'product':      product,
        'full_content': full_content,
        'user_id':      user_id,
    })

@staff_required
def delete_user_artwork(request, user_id, art_id):
    art = get_object_or_404(Artwork, pk=art_id, owner_id=user_id)
    # Получаем все страницы артворка для предпросмотра
    pages = art.pages.order_by('order').all()
    if request.method == 'POST':
        art.delete()
        messages.success(request, 'Артворк удалён')
        # При успешном удалении возвращаемся на страницу профиля
        return redirect('users_admin:user_detail', user_id=user_id)

    # Передаём user_id, чтобы шаблон мог построить правильную ссылку «Отмена»
    return render(request, 'admin/confirm_delete_user_artwork.html', {
        'art':     art,
        'pages':   pages,
        'user_id': user_id,
    })

@staff_required
def review_refund_requests(request):
    pending = RefundRequest.objects.filter(status='pending').order_by('-created_at')
    return render(request, 'admin/refunds.html', {'refunds': pending})


@staff_required
def resolve_refund_request(request, refund_id):
    refund = get_object_or_404(RefundRequest, pk=refund_id, status='pending')
    user = refund.user

    if refund.order_type == 'artwork':
        from .models import ArtworkOrder
        order = get_object_or_404(ArtworkOrder, pk=refund.order_id)
    else:
        from .models import TextProductOrder
        order = get_object_or_404(TextProductOrder, pk=refund.order_id)

    if request.method == 'POST':
        action = request.POST.get('action')
        comment = request.POST.get('moderator_comment', '').strip()
        refund.moderator_comment = comment

        if action == 'approve':
            # Возврат средств
            wallet = CryptoWallet.objects.get(user=user, currency=order.product.currency)
            wallet.balance += order.price
            wallet.save()

            # Удаление заказа
            order.delete()

            refund.status = 'approved'
            messages.success(request, "Жалоба одобрена, заказ отменён, средства возвращены.")

            Notification.objects.create(
                user=user,
                message=f"Ваша жалоба по заказу #{refund.order_id} принята: {comment}",
                link=reverse('portfolio')
            )

        elif action == 'reject':
            # Предупреждение
            user.profile.popularity -= 1  # символическое действие (можно заменить)
            user.save()

            # Если уже 3+ жалобы — бан
            prev_warnings = RefundRequest.objects.filter(user=user, status='rejected').count()
            refund.status = 'rejected'

            if prev_warnings + 1 >= 3:
                user.is_banned = True
                user.ban_reason = 'Чрезмерные ложные жалобы'
                user.save()
                messages.error(request, f"Пользователь {user.username} забанен за ложные жалобы.")
            else:
                messages.warning(request, f"Жалоба отклонена. Пользователю начислено предупреждение.")

            Notification.objects.create(
                user=user,
                message=f"Ваша жалоба по заказу #{refund.order_id} отклонена: {comment}",
                link=reverse('portfolio')
            )

        refund.save()
        return redirect('users_admin:review_refunds')

    return render(request, 'admin/resolve_refund.html', {
        'refund': refund,
        'order': order
    })

@staff_required
def disputes_list(request):
    qs = (Escrow.objects
          .filter(status=Escrow.Status.DISPUTED)
          .order_by('-dispute_created_at', '-id'))
    page = Paginator(qs, 30).get_page(request.GET.get('page') or 1)

    # аккуратно определим валюту и «красивую» сумму для каждой строки
    # (делаем это на Python-слое, чтобы не городить сложный annotate/Subquery)
    for e in page.object_list:
        try:
            order_model = e.order_ct.model_class()
            order = order_model.objects.select_related(
                "product","product__owner","artwork","artwork__owner"
            ).get(pk=e.order_id)
            curr = getattr(order.product, "currency", None) if hasattr(order, "product") else getattr(order.artwork, "currency", None)
        except Exception:
            curr = None

        e.currency = curr.name if hasattr(curr, "name") else (curr or "—")
        dec = micros_to_usd(int(e.amount_micros))
        s = format(dec.normalize(), 'f')
        if '.' in s:
            s = s.rstrip('0').rstrip('.')
        e.amount_pretty = f"{s} {e.currency}"

    return render(request, 'admin/disputes_list.html', {'page': page})


@staff_required
@require_POST
@transaction.atomic
def approve_refund(request, escrow_id: int):
    esc = get_object_or_404(Escrow.objects.select_for_update(), pk=escrow_id)
    if esc.status not in (Escrow.Status.HELD, Escrow.Status.DISPUTED):
        messages.info(request, "Этот спор уже обработан.")
        return redirect('users_admin:disputes_list')  # было: просто ставился флаг, но денег не трогали :contentReference[oaicite:0]{index=0}

    # 1) достаём заказ по contenttype (никаких поисков «по product_id») 
    order_model = esc.order_ct.model_class()
    order = order_model.objects.select_related(
        "product","product__owner","artwork","artwork__owner"
    ).get(pk=esc.order_id)

    # 2) валюта заказа
    currency = getattr(order.product, "currency", None) if hasattr(order, "product") else getattr(order.artwork, "currency", None)

    # 3) зафиксируем модераторское решение в связанном Dispute (если открыт)
    d = getattr(esc, "dispute", None)
    if d and d.is_open():
        d.moderator = request.user
        d.moderator_decision = "REFUND"
        d.decided_at = timezone.now()
        d.save(update_fields=["moderator","moderator_decision","decided_at"])

    # 4) деньги
    if currency == Currency.OSP:
        # ОФФЧЕЙН: леджер + красивая запись транзакции
        buyer_wallet = CryptoWallet.objects.select_for_update().get(user=esc.buyer, currency=Currency.OSP)
        amt_i = int(esc.amount_micros)

        # Леджерное зачисление (рекомендуемый тип — ESCROW_REFUND; falls back допустим)
        kind_refund = getattr(LedgerEntry.Kind, "ESCROW_REFUND", LedgerEntry.Kind.TOPUP_OSP_SOFT)
        book_osp(
            buyer_wallet,
            kind=kind_refund,
            reference=f"{esc.order_ct.model}:{esc.order_id}:refund",
            delta_micros=+amt_i,
        )

        # Человекочитаемый лог
        CryptoTransaction.objects.create(
            wallet=buyer_wallet,
            tx_type="refund",
            amount=Decimal(amt_i) / Decimal(1_000_000),
            amount_micros=amt_i,
            reference=f"{esc.order_ct.model}:{esc.order_id}",
        )

        # Статусы escrow/заказа
        esc.status = Escrow.Status.REFUNDED
        esc.refunded_at = timezone.now()
        esc.disputed = False
        esc.moderator_locked = False
        esc.save(update_fields=["status","refunded_at","disputed","moderator_locked"])

        # контент-доступ: для текстов снимаем “is_active”, для артов — аналогично при необходимости
        if hasattr(order, "is_active"):
            order.is_active = False
            order.save(update_fields=["is_active"])

        messages.success(request, "Возврат выполнен покупателю (OSP).")
        return redirect('users_admin:disputes_list')

    # USDT — ончейн
    executor = getattr(account, "address", None) or getattr(settings, "ESCROW_EXECUTOR_ADDRESS", None)
    if not executor:
        messages.error(request, "Не настроен адрес исполнителя ончейн-транзакций.")
        return redirect('users_admin:disputes_list')

    tx = build_refund_tx(esc.external_order_id, executor)
    tx_hash, _ = send_tx(tx)
    esc.refund_tx = tx_hash
    esc.moderator_locked = True
    esc.save(update_fields=["refund_tx","moderator_locked"])
    messages.success(request, "Refund-транзакция отправлена (USDT). Ожидаем подтверждения сети.")
    return redirect('users_admin:disputes_list')



def _get_order_for_escrow(esc: Escrow):
    """Надёжно достаём заказ по ContentType escrow’а (без гаданий по product_id)."""
    if esc.order_ct.model_class() is TextProductOrder:
        return TextProductOrder.objects.select_related("product","product__owner").get(pk=esc.order_id)
    else:
        return ArtworkOrder.objects.select_related("artwork","artwork__owner").get(pk=esc.order_id)



@staff_required
@require_POST
@transaction.atomic
def dispute_release(request, escrow_id: int):
    """Решение модератора: RELEASE (в пользу продавца)."""
    esc = get_object_or_404(Escrow.objects.select_for_update(), pk=escrow_id)
    if esc.status not in (Escrow.Status.HELD, Escrow.Status.DISPUTED):
        messages.info(request, "Эскроу уже обработан.")
        return redirect('users_admin:disputes_list')

    order = _get_order_for_escrow(esc)
    currency = getattr(order.product if hasattr(order, "product") else order.artwork, "currency", None)

    # зафиксируем решение в Dispute (если есть открытый)
    d = getattr(esc, "dispute", None)
    if d and d.is_open():
        d.moderator = request.user
        d.moderator_decision = "RELEASE"
        d.decided_at = timezone.now()
        d.save(update_fields=["moderator","moderator_decision","decided_at"])

    if currency == Currency.OSP:
        # ОФФЧЕЙН: помечаем RELEASED и проводим деньги продавцу (минус комиссия платформы)
        seller = getattr(order.product if hasattr(order, "product") else order.artwork, "owner")
        seller_wallet = CryptoWallet.objects.select_for_update().get(user=seller, currency=Currency.OSP)

        settle_sale_with_platform_fee_osp(
            total_amount_micros=int(esc.amount_micros),
            seller_wallet=seller_wallet,
            order_ref=f"{esc.order_ct.model}:{esc.order_id}"
        )

        esc.status = Escrow.Status.RELEASED
        esc.released_at = timezone.now()
        esc.disputed = False
        esc.moderator_locked = False
        esc.save(update_fields=["status","released_at","disputed","moderator_locked"])

        # Инвентарь: для артворка уменьшаем тираж при релизе (если не уменьшали ранее)
        if isinstance(order, ArtworkOrder):
            art = order.artwork
            if art.available_copies > 0:
                art.available_copies -= 1
                if art.available_copies <= 0:
                    art.is_active = False
                art.save(update_fields=["available_copies","is_active"])

        messages.success(request, "Средства выпущены продавцу (OSP).")
        return redirect('users_admin:disputes_list')

    # ОНЧЕЙН: строим транзакцию release(), эскроу обновится воркером после майнинга
    executor = getattr(account, "address", None) or getattr(settings, "ESCROW_EXECUTOR_ADDRESS", None)
    if not executor:
        messages.error(request, "Не настроен адрес исполнителя ончейн-транзакций.")
        return redirect('users_admin:disputes_list')

    tx = build_release_tx(esc.external_order_id, executor)
    tx_hash, _ = send_tx(tx)
    esc.release_tx = tx_hash
    esc.moderator_locked = True
    esc.save(update_fields=["release_tx","moderator_locked"])
    messages.success(request, "Release-транзакция отправлена (USDT). Ожидаем подтверждения сети.")
    return redirect('users_admin:disputes_list')


@staff_required
@require_POST
@transaction.atomic
def dispute_refund(request, escrow_id: int):
    """Решение модератора: REFUND (в пользу покупателя)."""
    esc = get_object_or_404(Escrow.objects.select_for_update(), pk=escrow_id)
    if esc.status not in (Escrow.Status.HELD, Escrow.Status.DISPUTED):
        messages.info(request, "Эскроу уже обработан.")
        return redirect('users_admin:disputes_list')

    order = _get_order_for_escrow(esc)
    currency = getattr(order.product if hasattr(order, "product") else order.artwork, "currency", None)

    # зафиксируем решение
    d = getattr(esc, "dispute", None)
    if d and d.is_open():
        d.moderator = request.user
        d.moderator_decision = "REFUND"
        d.decided_at = timezone.now()
        d.save(update_fields=["moderator","moderator_decision","decided_at"])

    if currency == Currency.OSP:
        # ОФФЧЕЙН: возвращаем средства покупателю с корректной бухгалтерией
        buyer_wallet = CryptoWallet.objects.select_for_update().get(user=esc.buyer, currency=Currency.OSP)

        kind_refund = getattr(LedgerEntry.Kind, "ESCROW_REFUND", LedgerEntry.Kind.TOPUP_OSP_SOFT)
        book_osp(
            buyer_wallet,
            kind=kind_refund,
            reference=f"{esc.order_ct.model}:{esc.order_id}:refund",
            delta_micros=int(esc.amount_micros)
        )

        # (опционально) запишем «человеческую» транзакцию
        CryptoTransaction.objects.create(
            wallet=buyer_wallet, tx_type="refund",
            amount_micros=int(esc.amount_micros), amount=Decimal(int(esc.amount_micros))/Decimal(1_000_000),
            reference=f"{esc.order_ct.model}:{esc.order_id}"
        )

        esc.status = Escrow.Status.REFUNDED
        esc.refunded_at = timezone.now()
        esc.disputed = False
        esc.moderator_locked = False
        esc.save(update_fields=["status","refunded_at","disputed","moderator_locked"])

        # доступ/контент: деактивируем заказ
        if hasattr(order, "is_active"):
            order.is_active = False
            order.save(update_fields=["is_active"])

        messages.success(request, "Возврат выполнен покупателю (OSP).")
        return redirect('users_admin:disputes_list')

    # ОНЧЕЙН: шлём refund(), ждём сеть
    executor = getattr(account, "address", None) or getattr(settings, "ESCROW_EXECUTOR_ADDRESS", None)
    if not executor:
        messages.error(request, "Не настроен адрес исполнителя ончейн-транзакций.")
        return redirect('users_admin:disputes_list')

    tx = build_refund_tx(esc.external_order_id, executor)
    tx_hash, _ = send_tx(tx)
    esc.refund_tx = tx_hash
    esc.moderator_locked = True
    esc.save(update_fields=["refund_tx","moderator_locked"])
    messages.success(request, "Refund-транзакция отправлена (USDT). Ожидаем подтверждения сети.")
    return redirect('users_admin:disputes_list')



@staff_required
@require_POST
def moderator_decision_release(request):
    data = request.POST
    kind = data.get("kind")
    obj_id = int(data.get("id", 0))
    if kind not in ("text", "art") or obj_id <= 0:
        return HttpResponseBadRequest("bad args")

    # находим escrow без привязки к пользователю
    if kind == "text":
        order = TextProductOrder.objects.filter(product_id=obj_id).order_by('-id').first()
    else:
        order = ArtworkOrder.objects.filter(artwork_id=obj_id).order_by('-id').first()
    escrow = getattr(order, 'escrow', None) if order else None
    if not escrow or escrow.status != Escrow.Status.HELD:
        return HttpResponseBadRequest("escrow not found or not HELD")

    curr = getattr(order.product if kind == "text" else order.artwork, "currency", None)

    with transaction.atomic():
        # фиксируем модераторское решение в Dispute
        d = getattr(escrow, "dispute", None)
        if d and d.is_open():
            d.moderator = request.user
            d.moderator_decision = "RELEASE"
            d.decided_at = timezone.now()
            d.save(update_fields=["moderator", "moderator_decision", "decided_at"])

        if curr == Currency.OSP:
            # оффчейн — закрываем локально
            if escrow.status == Escrow.Status.HELD:
                escrow.status = Escrow.Status.RELEASED
                escrow.released_at = timezone.now()
                escrow.disputed = False
                escrow.moderator_locked = False
                escrow.save(update_fields=["status", "released_at", "disputed", "moderator_locked"])
        else:
            executor = getattr(account, "address", None) or getattr(settings, "ESCROW_EXECUTOR_ADDRESS", None)
            if not executor:
                return HttpResponseBadRequest("executor address is not configured")

            # строим транзакцию релиза и отправляем
            tx = build_release_tx(escrow.external_order_id, executor)
            tx_hash, _ = send_tx(tx)  # send_tx возвращает (tx_hash_hex, receipt)
            escrow.release_tx = tx_hash
            escrow.save(update_fields=["release_tx"])

    return JsonResponse({"ok": True})


@staff_required
@require_POST
def moderator_decision_refund(request):
    """
    Решение модератора: REFUND (в пользу покупателя).
    USDT/он-чен: шлём refund(orderId).
    OSP/оффчейн: локально переводим в REFUNDED и возвращаем доступ согласно твоей логике.
    """
    data = request.POST
    kind = data.get("kind")
    obj_id = int(data.get("id", 0))
    if kind not in ("text", "art") or obj_id <= 0:
        return HttpResponseBadRequest("bad args")

    if kind == "text":
        order = TextProductOrder.objects.filter(product_id=obj_id).order_by('-id').first()
    else:
        order = ArtworkOrder.objects.filter(artwork_id=obj_id).order_by('-id').first()
    escrow = getattr(order, 'escrow', None) if order else None
    if not escrow or escrow.status != Escrow.Status.HELD:
        return HttpResponseBadRequest("escrow not found or not HELD")

    curr = getattr(order.product if kind == "text" else order.artwork, "currency", None)

    with transaction.atomic():
        d = getattr(escrow, "dispute", None)
        if d and d.is_open():
            d.moderator = request.user
            d.moderator_decision = "REFUND"
            d.decided_at = timezone.now()
            d.save(update_fields=["moderator", "moderator_decision", "decided_at"])

        if curr == Currency.OSP:
            if escrow.status == Escrow.Status.HELD:
                escrow.status = Escrow.Status.REFUNDED
                escrow.refunded_at = timezone.now()
                escrow.disputed = False
                escrow.moderator_locked = False
                escrow.save(update_fields=["status", "refunded_at", "disputed", "moderator_locked"])
                # если нужен возврат «доступа/баланса» оффчейн — дерни свою утилиту здесь
        else:
            executor = getattr(account, "address", None) or getattr(settings, "ESCROW_EXECUTOR_ADDRESS", None)
            if not executor:
                return HttpResponseBadRequest("executor address is not configured")

            tx = build_refund_tx(escrow.external_order_id, executor)
            tx_hash, _ = send_tx(tx)  # см. utils.send_tx
            escrow.refund_tx = tx_hash
            escrow.save(update_fields=["refund_tx"])

    return JsonResponse({"ok": True})

def _collect_open_escrows_for_product(product):
    # Пример: если есть модели заказов TextProductOrder/ArtworkOrder со связью на Escrow
    open_items = []
    # Text
    try:
        for o in TextProductOrder.objects.filter(product=product).select_related("escrow"):
            if o.escrow and o.escrow.status in ("HELD","DISPUTED","PENDING_RELEASE"):
                open_items.append(o.escrow)
    except Exception:
        pass
    return open_items

def _collect_open_escrows_for_artwork(artwork):
    open_items = []
    try:
        for o in ArtworkOrder.objects.filter(artwork=artwork).select_related("escrow"):
            if o.escrow and o.escrow.status in ("HELD","DISPUTED","PENDING_RELEASE"):
                open_items.append(o.escrow)
    except Exception:
        pass
    return open_items