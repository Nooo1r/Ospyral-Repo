from django import template
register = template.Library()
from django.urls import reverse


@register.filter
def instanceof(obj, class_name):
    try:
        return obj.__class__.__name__ == class_name
    except Exception:
        return False

@register.filter
def classname(obj):
    return obj.__class__.__name__

@register.filter
def edit_url(obj):
    # Для TextProduct
    if obj.__class__.__name__ == 'TextProduct':
        return reverse('add_text_product') + f'?edit={obj.id}'
    # Для Artwork
    elif obj.__class__.__name__ == 'Artwork':
        return reverse('edit_artwork', kwargs={'artwork_id': obj.id})
    # Для других случаев
    return '#'