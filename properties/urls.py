from django.urls import path

from . import views

app_name = "properties"

urlpatterns = [
    path("", views.property_list, name="list"),
    path("buy/", views.property_list, {"preset": "buy"}, name="buy"),
    path("rent/", views.property_list, {"preset": "rent"}, name="rent"),
    path("land/", views.property_list, {"preset": "land"}, name="land"),
    path("commercial/", views.property_list, {"preset": "commercial"}, name="commercial"),
    path("hot-deals/", views.property_list, {"preset": "deals"}, name="deals"),
    path("search/suggest/", views.search_suggest, name="suggest"),
    path("submit/", views.submit_property, name="submit"),
    path("submit/thank-you/", views.submit_done, name="submit_done"),
    path("submit/status/", views.submission_status, name="status"),
    path("<slug:slug>/", views.property_detail, name="detail"),
]
