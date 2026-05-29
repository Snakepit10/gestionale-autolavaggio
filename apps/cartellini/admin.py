from django.contrib import admin

from .models import SetCartellini


@admin.register(SetCartellini)
class SetCartelliniAdmin(admin.ModelAdmin):
    list_display = ('nome', 'descrizione', 'num_cartellini', 'creato_da', 'updated_at')
    list_filter = ('creato_da',)
    search_fields = ('nome', 'descrizione')
    readonly_fields = ('created_at', 'updated_at')
