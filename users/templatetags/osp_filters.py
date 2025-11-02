from django import template

register = template.Library()

@register.filter
def osp_from_micros(value: int) -> str:
    """
    Преобразует микросы (μOSP) в удобный вид для отображения.
    Пример: 20000000 → "20 OSP"
    """
    if value is None:
        return "0 OSP"

    try:
        value = int(value)
    except (TypeError, ValueError):
        return "?"

    whole = value // 1_000_000
    frac  = value % 1_000_000
    if frac == 0:
        return f"{whole} OSP"
    s = f"{frac:06d}".rstrip('0')
    return f"{whole}.{s} OSP"