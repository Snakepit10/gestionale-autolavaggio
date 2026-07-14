"""Adapter django-allauth per il login social clienti (Google).

Responsabilita':
- collegare il login Google a un account esistente con la stessa email
  (registrazione classica o scheda Cliente creata in cassa) invece di
  duplicare;
- creare/collegare la scheda Cliente al primo accesso social;
- dopo il login, mandare il cliente a completare il profilo se manca
  il telefono (obbligatorio), altrimenti alla sua dashboard.
"""
from django.contrib.auth.models import User
from django.urls import reverse

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

from apps.clienti.models import Cliente


def _redirect_post_login_cliente(user):
    cliente = getattr(user, 'cliente', None)
    if cliente is not None and not (cliente.telefono or '').strip():
        return reverse('auth:completa-profilo')
    return reverse('clients:dashboard')


class ClienteAccountAdapter(DefaultAccountAdapter):
    def get_login_redirect_url(self, request):
        if hasattr(request.user, 'cliente'):
            return _redirect_post_login_cliente(request.user)
        return super().get_login_redirect_url(request)


class ClienteSocialAdapter(DefaultSocialAccountAdapter):
    def pre_social_login(self, request, sociallogin):
        """Auto-collegamento a un User esistente con la stessa email.

        Sicuro perche' l'email che arriva da Google e' verificata da
        Google stesso: chi controlla quella casella e' il legittimo
        proprietario dell'account con la stessa email.
        """
        if sociallogin.is_existing:
            return
        email = (sociallogin.user.email or '').strip()
        if not email:
            return
        utente = User.objects.filter(email__iexact=email).order_by('pk').first()
        if utente is not None:
            sociallogin.connect(request, utente)

    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form)
        self._collega_cliente(user)
        return user

    @staticmethod
    def _collega_cliente(user):
        """Crea o collega la scheda Cliente al nuovo User social."""
        if getattr(user, 'cliente', None) is not None:
            return
        cliente = Cliente.objects.filter(
            email__iexact=user.email, user__isnull=True,
        ).order_by('pk').first()
        if cliente is not None:
            cliente.user = user
            if not cliente.nome:
                cliente.nome = user.first_name or cliente.nome
            if not cliente.cognome:
                cliente.cognome = user.last_name or ''
            cliente.save()
        else:
            Cliente.objects.create(
                tipo='privato',
                nome=user.first_name or user.email.split('@')[0],
                cognome=user.last_name or '',
                email=user.email,
                telefono='',
                user=user,
            )
