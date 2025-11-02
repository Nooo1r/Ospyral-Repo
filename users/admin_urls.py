
from django.urls import path
from . import admin_views as views

app_name = 'users_admin'

urlpatterns = [
    path('', views.admin_index, name='admin_index'),

    #УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ
    path('users/', views.user_search, name='user_search'),
    path('users/<int:user_id>/', views.user_detail, name='user_detail'),
    path('users/<int:user_id>/ban/',   views.ban_user,   name='ban_user'),
    path('users/<int:user_id>/unban/', views.unban_user, name='unban_user'),
    path('users/<int:user_id>/vip/grant/', views.grant_vip, name='grant_vip'),
    path('users/<int:user_id>/vip/revoke/', views.revoke_vip, name='revoke_vip'),
    
    path("news/", views.news_list, name="news_list"),
    path("news/create/", views.news_create, name="news_create"),
    path("news/<int:pk>/edit/", views.news_edit, name="news_edit"),
    path("news/<int:pk>/delete/", views.news_delete, name="news_delete"),
    path("news/<int:pk>/publish_now/", views.news_publish_now, name="news_publish_now"),
    path("news/<int:pk>/unpublish/",   views.news_unpublish,   name="news_unpublish"),
    
    path("rewards/osp/", views.admin_grant_osp, name="grant_osp"),
    path("rewards/osp/success/", views.admin_grant_osp_success, name="grant_osp_success"),


    #ТЕКСТОВАЯ ПРОДУКЦИЯ
    path('text-products/', views.review_text_products, name='review_text_products'),
    path('text-products/<int:product_id>/', views.text_product_detail, name='text_product_detail'),
    path('text-products/<int:product_id>/approve/', views.approve_text_product, name='approve_text_product'),
    path('text-products/<int:product_id>/reject/',  views.reject_text_product,  name='reject_text_product'),
    path('users/<int:user_id>/text-products/<int:product_id>/delete/', views.delete_user_text_product, name='delete_user_text_product'),


    #АРТВОРКИ
    path('artworks/', views.review_artworks, name='review_artworks'),
    path('artworks/<int:art_id>/', views.artwork_detail, name='artwork_detail'),
    path('artworks/<int:art_id>/approve/', views.approve_artwork, name='approve_artwork'),
    path('artworks/<int:art_id>/reject/', views.reject_artwork, name='reject_artwork'),
    path('users/<int:user_id>/artworks/<int:art_id>/delete/', views.delete_user_artwork, name='delete_user_artwork'),


    # СПОРЫ / ESCROW
    path('disputes/', views.disputes_list, name='disputes_list'),
    path('disputes/<int:escrow_id>/release/', views.dispute_release, name='dispute_release'),
    path('disputes/<int:escrow_id>/refund/',  views.dispute_refund,  name='dispute_refund'),


    #ПЛАТЕЖНАЯ СИСТЕМА
    path('refunds/', views.review_refund_requests, name='review_refunds'),
    path('refunds/<int:refund_id>/resolve/', views.resolve_refund_request, name='resolve_refund'),
    path('disputes/', views.disputes_list, name='disputes_list'),
    path('disputes/<int:escrow_id>/approve_refund/', views.approve_refund, name='approve_refund'),
    path('api/disputes/release', views.moderator_decision_release, name='mod_release'),
    path('api/disputes/refund',  views.moderator_decision_refund,  name='mod_refund'),
]
