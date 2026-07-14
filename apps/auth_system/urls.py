from django.urls import path
from . import views

app_name = 'auth'

urlpatterns = [
    # Login Operatori
    path('operatori/login/', views.operator_login, name='operator-login'),
    path('operatori/logout/', views.operator_logout, name='operator-logout'),
    
    # Login Clienti
    path('clienti/login/', views.client_login, name='client-login'),
    path('clienti/logout/', views.client_logout, name='client-logout'),
    path('clienti/dashboard/', views.ClientDashboardView.as_view(), name='client-dashboard'),

    # Recupero password clienti
    path('clienti/password/reset/',
         views.ClientPasswordResetView.as_view(), name='client-password-reset'),
    path('clienti/password/reset/inviata/',
         views.ClientPasswordResetDoneView.as_view(), name='client-password-reset-done'),
    path('clienti/password/reset/<uidb64>/<token>/',
         views.ClientPasswordResetConfirmView.as_view(), name='client-password-reset-confirm'),
    path('clienti/password/reset/completata/',
         views.ClientPasswordResetCompleteView.as_view(), name='client-password-reset-complete'),
]