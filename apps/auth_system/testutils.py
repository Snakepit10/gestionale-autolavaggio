"""Aiuti per i test: utenti del gestionale con le sezioni del titolare."""
from django.contrib.auth.models import Group, User


def utente_titolare(*args, **kwargs):
    """Come User.objects.create_user, poi nel gruppo titolare (che nei
    permessi iniziali vede tutte le sezioni)."""
    user = User.objects.create_user(*args, **kwargs)
    user.groups.add(Group.objects.get_or_create(name='titolare')[0])
    return user
