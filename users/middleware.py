from django.shortcuts import redirect
from django.utils.deprecation import MiddlewareMixin
from django.urls import reverse, NoReverseMatch
import time
from django.core.cache import cache
from django.http import HttpResponse

from django.utils import timezone
from django.conf import settings
from django.db import models
import logging
logger = logging.getLogger(__name__)

EXEMPT_URLS = [
    '/logout/',
    '/banned/',
]

WINDOW_SEC = 60
LIMITS = {
    "/login/": (10, WINDOW_SEC),
    "/register/": (6, WINDOW_SEC),
    "/verify-email/": (12, WINDOW_SEC),
    "/wallets/connect/start/": (10, WINDOW_SEC),
    "/wallets/connect/verify/": (10, WINDOW_SEC),
    "/api/orders/prepare": (20, WINDOW_SEC),
    "/api/orders/confirm": (20, WINDOW_SEC),
    "/api/disputes/open": (10, WINDOW_SEC),
    "/api/disputes/cancel": (10, WINDOW_SEC),
}

class RateLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST":
            path = request.path
            for k, (limit, window) in LIMITS.items():
                if path.startswith(k):
                    user = getattr(request, "user", None)
                    if user is not None and getattr(user, "is_authenticated", False):
                        ident_user = f"user:{user.id}"
                    else:
                        # если за прокси/Cloudflare – можно взять реальный IP из заголовка
                        ip = request.META.get("HTTP_CF_CONNECTING_IP") or request.META.get("REMOTE_ADDR")
                        ident_user = f"ip:{ip or '0.0.0.0'}"

                    ident = f"{path}:{ident_user}"
                    key = f"rl:{ident}:{int(time.time() // window)}"

                    count = cache.get(key, 0) + 1
                    cache.set(key, count, timeout=window + 5)
                    if count > limit:
                        return HttpResponse("Too Many Requests", status=429)
                    break
        return self.get_response(request)


class CheckBanMiddleware(MiddlewareMixin):
    def process_request(self, request):
        # 1) Анти-обход: отпечатки для всех (и анонимов тоже).
        from .models import BannedFingerprint
        import hashlib

        ip = (request.META.get("REMOTE_ADDR") or "").strip()
        ua = (request.META.get("HTTP_USER_AGENT") or "").strip()
        dev = request.COOKIES.get("osp_device_id", "")  # если внедрим клиентскую метку

        def h(x: str) -> str:
            return hashlib.sha256(x.encode("utf-8")).hexdigest() if x else ""

        ip_h, ua_h = h(ip), h(ua)

        # Если есть активные совпадения по любому признаку — режем доступ.
        if BannedFingerprint.objects.filter(
            models.Q(ip_hash=ip_h) | models.Q(ua_hash=ua_h) | models.Q(device_id=dev),
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=timezone.now())
        ).exists():
            # Разрешаем только страницу бана и выход
            if not (request.path.startswith("/banned/") or request.path.startswith("/logout/")):
                return redirect("banned")

        user = getattr(request, "user", None)

        # 2) Пользовательский бан: не пускаем забаненного никуда, кроме /banned/ и /logout/
        if user and user.is_authenticated and getattr(user, "is_banned", False):
            # авто-анбан по истекшему сроку
            if user.ban_until and timezone.now() >= user.ban_until:
                user.is_banned = False
                user.ban_reason = ""
                user.ban_until = None
                user.save(update_fields=["is_banned", "ban_reason", "ban_until"])
                return None

            if not (request.path.startswith("/banned/") or request.path.startswith("/logout/")):
                return redirect("banned")

        return None
    

class LoginRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

        # Безопасно берём именованные URL (могут отсутствовать)
        def named(path_name):
            try:
                return reverse(path_name)
            except NoReverseMatch:
                return None

        self.home_url     = named('home') or '/'
        # login_url из настроек имеет приоритет
        self.login_url    = getattr(settings, 'LOGIN_URL', None) or named('login') or '/login/'
        register_url      = named('register')
        verify_url        = named('verify_email')

        # Точные исключения (exact)
        self.exempt_exact = {p for p in ["/", self.home_url, self.login_url, register_url, verify_url] if p}

        # Префиксы-исключения
        self.exempt_prefixes = [
            '/static/',
            '/media/',
            '/admin/',        # стандартная админка + её статика
        ]

        # Анонимные API-префиксы (из настроек)
        self.anon_api_prefixes = list(getattr(settings, 'ANON_API_PREFIXES', []))

        # Дополнительные исключения (строки оканчивающиеся на '/' трактуем как префиксы)
        for p in getattr(settings, 'LOGIN_EXEMPT_URLS', []):
            if isinstance(p, str) and p.endswith('/'):
                self.exempt_prefixes.append(p)
            elif isinstance(p, str):
                self.exempt_exact.add(p)

    def __call__(self, request):
        path = request.path_info or request.path

        # Уже аутентифицирован — пропускаем дальше (там @staff_required разрулит)
        if request.user.is_authenticated:
            return self.get_response(request)

        # Разрешённые точные пути
        if path in self.exempt_exact:
            return self.get_response(request)

        # Разрешённые префиксы
        for pref in (self.exempt_prefixes + self.anon_api_prefixes):
            if path.startswith(pref):
                return self.get_response(request)

        # Аноним — отправляем на LOGIN_URL, добавляя next=...
        try:
            # защищаемся от бесконечного цикла: если и так на login — пропускаем
            if path.startswith(self.login_url):
                return self.get_response(request)
        except Exception:
            pass

        next_qs = f"?next={path}"
        # Если у login уже есть querystring, корректнее добавить через & — но обычно нет
        return redirect(f"{self.login_url}{next_qs}")