from django.conf import settings


def meta_pixel(request):
    """Espone l'ID del Meta Pixel ai template pubblici. Vuoto =
    pixel spento (il tag non viene nemmeno renderizzato)."""
    return {'META_PIXEL_ID': settings.META_PIXEL_ID}
