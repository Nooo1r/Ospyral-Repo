from django.urls import path, include
from . import views 
from django.contrib import admin
from django.views.generic import RedirectView
from .views import wallets_view, osp_deposit_instructions, edit_text_product


urlpatterns = [
    # Главная и аутентификация
    path('', views.home_view, name='home'),
    path('test-form/', views.test_form, name='test_form'),
    path('register/', views.register_view, name='register'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout_view'),
    path('favicon.ico', RedirectView.as_view(url='/static/images/favinspyral.png', permanent=True)),
    path('rules/', views.rules_overview, name='rules_overview'),
    path('verify-email/', views.verify_email, name='verify_email'),

    # Пользовательский профиль
    path('dashboard/', views.dashboard, name='dashboard'),
    path('edit_profile/', views.edit_profile, name='edit_profile'),
    path('user/<str:username>/', views.user_profile_view, name='user_profile'),
    path('save-background-color/', views.save_background_color, name='save_background_color'),
    path('portfolio/', views.portfolio, name='portfolio'),
    path('api/purchase/', views.api_create_purchase),
    path('wallets/', wallets_view, name='wallets'),
    path('wallets/osp/deposit/', osp_deposit_instructions, name='osp-deposit'),
    path('wallets/osp/deposit/<int:amount>/', osp_deposit_instructions, name='osp-deposit-amount'),
    path('purchase/success/<int:order_id>/', views.purchase_success, name='purchase_success'),

    


    # Текстовая продукция    
    path('text-products/<int:pk>/edit/', edit_text_product, name='edit_text_product'),
    path('text-products/<int:pk>/submit/', views.submit_text_product,   name='submit_text_product'),
    path('text-products/<int:pk>/owner/', views.owner_text_product, name='owner_text_product'),


    path('text-products/<int:pk>/delete/', views.delete_text_product,   name='delete_text_product'),
    path('text-products/<int:pk>/stop-sale/', views.stop_sale_text_product, name='stop_sale_text_product'),
    path('text-products/<int:pk>/resume/', views.resume_sale_text_product, name='resume_sale_text_product'),

    path('text-products/<int:pk>/ack-reject/', views.acknowledge_rejection, name='acknowledge_rejection'),
    path('text-products/public/<int:product_id>/', views.public_view_text_product, name='public_view_text_product'),

    path('text-products/catalog/', views.catalog_text_products, name='catalog_text_products'),
    path('text-products/add/', views.add_text_product, name='add_text_product'),
    path('text-products/<int:product_id>/', views.view_text_product, name='view_text_product'),
    path('text-products/<int:product_id>/rate/', views.rate_text_product, name='rate_text_product'),
    path('buy/text/<int:product_id>/', views.buy_text_product, name='buy_text_product'),




    # Галерея изображений
    path('art/<int:pk>/delete/', views.delete_artwork, name='delete_artwork'),
    path('art/<int:pk>/pause/',  views.pause_artwork,  name='pause_artwork'),
    path('art/<int:pk>/resume/', views.resume_artwork, name='resume_artwork'),

    path('notifications/', views.notifications_list, name='notifications_list'),

    path('artwork/store/', views.artwork_store, name='artwork_store'),
    path('artwork/page/<int:page_id>/full/', views.serve_full_page, name='serve_full_page'),
    path('artworks/', views.artwork_list, name='artwork_list'),
    path('artwork/<int:artwork_id>/my/', views.view_artwork_private, name='view_artwork_private'),
    path('artwork/<int:artwork_id>/', views.view_artwork_public, name='view_artwork_public'),


    path('artwork/page/<int:page_id>/save/', views.save_censored_page, name='save_censored_page'),
    # path('artwork/<int:artwork_id>/bid/', views.bid_artwork, name='bid_artwork'),
    path('artwork/<int:pk>/submit/',       views.submit_for_review,    name='submit_for_review'),
    path('artwork/create/', views.artwork_create, name='artwork_create'),
    path('artwork/<int:pk>/pages/', views.artwork_pages, name='artwork_pages'),
    path('artwork/<int:pk>/pages/create/', views.create_artwork_page, name='create_artwork_page'),
    path('artwork/pages/<int:page_id>/delete/', views.delete_artwork_page, name='delete_artwork_page'),
    path('artwork/pages/<int:page_id>/censor/', views.censor_artwork_page, name='censor_artwork_page'),
    path('artwork/<int:pk>/detail/', views.artwork_detail, name='artwork_detail'),
    path('buy/artwork/<int:artwork_id>/', views.buy_artwork, name='buy_artwork'),

    # Уроки
    path('lessons/', views.lesson_list, name='lesson_list'),
    path('lesson/add/', views.add_lesson, name='add_lesson'),
    path('lesson/edit/<int:lesson_id>/', views.edit_lesson, name='edit_lesson'),
    path('lesson/enroll/<int:lesson_id>/', views.enroll_lesson, name='enroll_lesson'),

    # Администрирование пользовательской текстовой продукции
    path('secret-admin/', include('users.admin_urls')),  
]