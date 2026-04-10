from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied


def utente_nel_gruppo(user, *gruppi):
    """Verifica se l'utente appartiene ad almeno uno dei gruppi indicati."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=gruppi).exists()


class GruppoRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """
    Mixin base: richiede login e appartenenza ai gruppi specificati in `gruppi_richiesti`.
    Usare come primo mixin nella MRO della view.
    """
    gruppi_richiesti = []

    def test_func(self):
        return utente_nel_gruppo(self.request.user, *self.gruppi_richiesti)

    def handle_no_permission(self):
        raise PermissionDenied


class TitolareRequiredMixin(GruppoRequiredMixin):
    gruppi_richiesti = ['titolare']


class ResponsabileOTitolareMixin(GruppoRequiredMixin):
    gruppi_richiesti = ['responsabile', 'titolare']


class QualsivogliaOperatoreMixin(GruppoRequiredMixin):
    gruppi_richiesti = ['operatore', 'responsabile', 'titolare']
