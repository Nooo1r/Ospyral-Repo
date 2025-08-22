
from django.contrib.auth.models import AnonymousUser

def unread_notifications(request):
    user = getattr(request, "user", AnonymousUser())
    count = 0
    if getattr(user, "is_authenticated", False):
        count = 0
    return {"unread_notifications": count}
