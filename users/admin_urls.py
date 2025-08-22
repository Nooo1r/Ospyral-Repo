
from django.urls import path
from . import admin_views as views

app_name = 'users_admin'

urlpatterns = [
    path('', views.admin_index, name='admin_index'),

    #ТЕКСТОВАЯ ПРОДУКЦИЯ
    path('text-products/', views.review_text_products, name='review_text_products'),
    path('text-products/<int:product_id>/', views.text_product_detail, name='text_product_detail'),
    path('text-products/<int:product_id>/approve/', views.approve_text_product, name='approve_text_product'),
    path('text-products/<int:product_id>/reject/',  views.reject_text_product,  name='reject_text_product'),
    
    #АРТВОРКИ
    path('artworks/', views.review_artworks, name='review_artworks'),
    path('artworks/<int:art_id>/', views.artwork_detail,    name='artwork_detail'),
    path('artwork/<int:art_id>/approve/', views.approve_artwork, name='approve_artwork'),
    path('artworks/<int:art_id>/', views.artwork_detail, name='artwork_detail'),
    path('artworks/<int:art_id>/reject/', views.reject_artwork, name='reject_artwork'),

    path('users/', views.user_search, name='user_search'),

    path('users/<int:user_id>/', views.user_detail, name='user_detail'),

    path('users/<int:user_id>/block/',   views.block_user,   name='block_user'),
    path('users/<int:user_id>/unblock/', views.unblock_user, name='unblock_user'),

    path(
        'users/<int:user_id>/text-products/<int:product_id>/delete/',
        views.delete_user_text_product,
        name='delete_user_text_product'
    ),

    path(
        'users/<int:user_id>/artworks/<int:art_id>/delete/',
        views.delete_user_artwork,
        name='delete_user_artwork'
    ),
    
    path('refunds/', views.review_refund_requests, name='review_refunds'),
    path('refunds/<int:refund_id>/resolve/', views.resolve_refund_request, name='resolve_refund'),
]
