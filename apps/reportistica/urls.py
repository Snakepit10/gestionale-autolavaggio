from django.urls import path
from . import views

app_name = 'reportistica'

urlpatterns = [
    # Dashboard principale
    path('', views.DashboardPrincipaleView.as_view(), name='dashboard'),
    
    # Report
    path('report/', views.ReportListView.as_view(), name='report-list'),
    path('report/<int:pk>/', views.ReportDetailView.as_view(), name='report-detail'),
    path('report/<int:report_id>/genera/', views.genera_report, name='genera-report'),
    
    # API per dati real-time
    path('api/kpi/', views.api_kpi_data, name='api-kpi'),
    
    # Esportazioni
    path('export/completo/', views.esporta_dati_completi, name='export-completo'),
]