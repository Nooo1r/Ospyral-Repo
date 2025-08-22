from django.shortcuts import render
from django.utils.deprecation import MiddlewareMixin
from django.urls import reverse
from django.utils import timezone

EXEMPT_URLS = [
    '/logout/',
    '/banned/',
]

class CheckBanMiddleware(MiddlewareMixin):
    def process_request(self, request):
        user = request.user
        if user.is_authenticated and user.is_banned:
            # если есть срок и он истёк, сбросим бан
            if user.ban_until and timezone.now() >= user.ban_until:
                user.is_banned = False
                user.ban_reason = ''
                user.ban_until = None
                user.save(update_fields=['is_banned','ban_reason','ban_until'])
                return None

            path = request.path
            if not any(path.startswith(u) for u in EXEMPT_URLS):
                return render(request, 'banned.html', status=403)
        return None