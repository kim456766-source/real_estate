from django.urls import path  # noqa: F401

app_name = "appointments"

# The public "Request a visit" form was removed: clients ask through the property's message
# conversation instead. Agents still manage visits from the dashboard (Appointments).
urlpatterns = []
