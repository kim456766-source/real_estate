from django.urls import path

from . import views

app_name = "inquiries"

urlpatterns = [
    path("send/<slug:slug>/", views.submit_inquiry, name="submit"),
    path("<int:pk>/reply/", views.client_reply, name="reply"),
    path("conversations/", views.conversations, name="conversations"),
    path("chat/<slug:slug>/send/", views.chat_send, name="chat_send"),
    path("chat/<int:pk>/messages/", views.chat_poll, name="chat_poll"),
]
