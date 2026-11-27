from django.urls import path

from . import insights, views

app_name = "dashboard"

urlpatterns = [
    path("", views.home, name="home"),
    # Properties
    path("properties/", views.property_list, name="properties"),
    path("properties/new/", views.property_create, name="property_create"),
    path("properties/<int:pk>/edit/", views.property_edit, name="property_edit"),
    path("properties/<int:pk>/delete/", views.property_delete, name="property_delete"),
    path("properties/<int:pk>/review/", views.property_review, name="property_review"),
    path("properties/<int:pk>/status/<str:status>/", views.property_quick_status, name="property_status"),
    path("properties/<int:pk>/availability/<str:status>/", views.property_availability, name="property_availability"),
    path("properties/<int:pk>/assign/", views.property_assign, name="property_assign"),
    path("properties/<int:pk>/hot-deal/", views.property_hot_deal, name="property_hot_deal"),
    path("properties/<int:pk>/repost/", views.property_repost, name="property_repost"),
    path("properties/<int:pk>/release/", views.property_release, name="property_release"),
    path("properties/<int:pk>/share/", views.property_share, name="property_share"),
    path("properties/<int:pk>/photos.zip", views.property_photos_zip, name="property_photos"),
    path("images/<int:pk>/delete/", views.image_delete, name="image_delete"),
    path("images/<int:pk>/cover/", views.image_cover, name="image_cover"),
    # People
    path("clients/", views.client_contacts, name="clients"),
    path("owners/", views.owner_contacts, name="owners"),
    path("users/<str:role>/", views.user_list, name="users"),
    path("user/<int:pk>/", views.user_detail, name="user_detail"),
    path("user/<int:pk>/action/<str:action>/", views.user_action, name="user_action"),
    path("user/<int:pk>/password/", views.user_set_password, name="user_set_password"),
    path("agents/new/", views.agent_create, name="agent_create"),
    path("admins/new/", views.admin_create, name="admin_create"),
    path("agent-applications/", views.agent_applications, name="agent_applications"),
    path("agent-applications/<int:pk>/<str:decision>/", views.agent_application_decide, name="agent_application_decide"),
    # Inquiries & appointments
    path("inquiries/", views.inquiry_list, name="inquiries"),
    path("inquiries/<int:pk>/", views.inquiry_detail, name="inquiry_detail"),
    path("appointments/", views.appointment_list, name="appointments"),
    path("appointments/<int:pk>/", views.appointment_detail, name="appointment_detail"),
    # Payments
    path("payments/", views.payment_list, name="payments"),
    path("payments/new/", views.payment_create, name="payment_create"),
    path("payments/<int:pk>/", views.payment_detail, name="payment_detail"),
    path("payments/<int:pk>/<str:action>/", views.payment_action, name="payment_action"),
    # Reports & settings
    path("reports/", views.reports, name="reports"),
    path("reports/export/<str:kind>.csv", views.export_csv, name="export"),
    path("settings/", views.site_settings, name="settings"),
    path("categories/<int:pk>/delete/", views.category_delete, name="category_delete"),
    path("settings/test-email/", views.send_test_email, name="test_email"),
    path("messages/", views.contact_messages, name="messages"),
    path("audit/", views.audit_logs, name="audit"),
    # Insights, documents, help
    path("active-users/", insights.active_users, name="active_users"),
    path("analytics/", insights.analytics, name="analytics"),
    path("documents/", insights.documents, name="documents"),
    path("documents/<int:pk>/", insights.document_download, name="document_download"),
    path("documents/<int:pk>/delete/", insights.document_delete, name="document_delete"),
    path("problem-reports/", insights.problem_reports, name="problem_reports"),
    path("problem-reports/<int:pk>/<str:action>/", insights.problem_report_action, name="problem_report_action"),
    path("help/", insights.help_page, name="help"),
]
