# users/admin_views.py

from django.shortcuts import render, get_object_or_404, redirect
from django.core.exceptions import PermissionDenied
from django.contrib.auth.decorators import user_passes_test
from .models import (
    TextProduct, 
    Artwork, 
    Notification, 
    CustomUser, 
    RefundRequest,
    CryptoWallet
    )
from django.utils import timezone
from django.contrib import messages
from django.urls import reverse
from .utils   import decrypt_text


# Декоратор, ограничивающий доступ только staff-пользователям
staff_required = user_passes_test(lambda u: u.is_active and u.is_staff)

@staff_required
def admin_index(request):
    return render(request, 'admin/admin_index.html')

@staff_required
def review_text_products(request):
    # Здесь используем статус PENDING (на рассмотрении)
    products = TextProduct.objects.filter(status=TextProduct.Status.PENDING) \
                                  .order_by('-created_at')
    return render(request, 'admin/review_text_products.html', {'products': products})


@staff_required
def text_product_detail(request, product_id):
    product = get_object_or_404(TextProduct, id=product_id)
    # гарантируем, что админ всегда может смотреть любой текст
    # (поэтому проверки owner/request.user.is_staff можно опустить)
    # Если нужен ещё более глубокий доступ (например, дешифровка), можно повторить логику из обычного detail
    try:
        from .utils import decrypt_text
        full_content = decrypt_text(product.encrypted_content, product.owner)
    except ImportError:
        full_content = product.encrypted_content

    return render(request, 'admin/text_product_detail.html', {
        'product': product,
        'full_content': full_content
    })

@staff_required
def approve_text_product(request, product_id):
    product = get_object_or_404(TextProduct, id=product_id)
    
    if request.method == "POST":
        try:
            quality = int(request.POST.get("quality", 0))
            uniqueness = int(request.POST.get("uniqueness", 0))
            spelling = int(request.POST.get("spelling", 0))
        except ValueError:
            messages.error(request, "Введите корректные числовые значения.")
            return redirect('admin/approve_text_product', product_id=product.id)
        
        if not (0 <= quality <= 100 and 0 <= uniqueness <= 100 and 0 <= spelling <= 100):
            messages.error(request, "Значения должны быть от 0 до 100.")
            return redirect('admin/approve_text_product', product_id=product.id)
        
        product.quality = quality
        product.uniqueness = uniqueness
        product.spelling = spelling
        product.mark_approved()
        product.save()
        
        messages.success(request, "Продукт одобрен и опубликован!")
        return redirect('users_admin:review_text_products')
    
    context = {
        'product': product,
    }
    return render(request, 'admin/approve_text_product.html', context)

@staff_required
def reject_text_product(request, product_id):
    product = get_object_or_404(
        TextProduct,
        id=product_id,
        status=TextProduct.Status.PENDING
    )
    if request.method == 'POST':
        title = product.title
        product.mark_rejected(reason="Отклонено администратором") 
        messages.warning(request, f'Продукт "{title}" отклонён и удалён.')
        return redirect('users_admin:review_text_products')
    # GET: показываем страницу подтверждения отклонения
    return render(request, 'admin/reject_text_product.html', {'product': product})



@staff_required
def review_artworks(request):
    pending = Artwork.objects.filter(status=Artwork.Status.PENDING) \
                            .order_by('-created_at')
    return render(request, 'admin/review_artworks.html', {'pending': pending})


@staff_required
def artwork_detail(request, art_id):
    art = get_object_or_404(Artwork, pk=art_id)
    return render(request, 'admin/artwork_detail.html', {'art': art})


@staff_required
def approve_artwork(request, art_id):
    art = get_object_or_404(
        Artwork,
        id=art_id,
        status=Artwork.Status.PENDING
    )
    art.status      = Artwork.Status.APPROVED
    art.is_approved = True
    art.approved_at = timezone.now()
    art.save(update_fields=['status', 'is_approved', 'approved_at'])
    return redirect('users_admin:review_artworks')

@staff_required
def reject_artwork(request, art_id):
    art = get_object_or_404(
        Artwork,
        id=art_id,
        status=Artwork.Status.PENDING
    )
    if request.method == 'POST':
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
           message=(
               f'Ваш артворк «{art.title}» отклонён администратором: «{reason}»'
           ),
           link=reverse('portfolio') + '?tab=rejected'
        )

        messages.warning(request, f'Артворк "{art.title}" отклонён.')
        return redirect('users_admin:review_artworks')
    return render(request, 'admin/reject_artwork.html', {'art': art})

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
        'q': q,
    })

@staff_required
def user_detail(request, user_id):
    user_obj       = get_object_or_404(CustomUser, pk=user_id)
    text_products  = TextProduct.objects.filter(owner=user_obj)
    artworks       = Artwork.objects.filter(owner=user_obj)
    return render(request, 'admin/user_detail.html', {
        'user_obj':      user_obj,
        'text_products': text_products,
        'artworks':      artworks,
    })

@staff_required
def block_user(request, user_id):
    user_obj = get_object_or_404(CustomUser, pk=user_id)
    user_obj.is_active = False
    user_obj.save(update_fields=['is_active'])
    messages.success(request, f'Пользователь «{user_obj.username}» заблокирован')
    return redirect('users_admin:user_detail', user_id=user_id)

@staff_required
def unblock_user(request, user_id):
    user_obj = get_object_or_404(CustomUser, pk=user_id)
    user_obj.is_active = True
    user_obj.save(update_fields=['is_active'])
    messages.success(request, f'Пользователь «{user_obj.username}» разблокирован')
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
