from django.urls import path
from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("register/", views.register, name="register"),
    path("library/", views.library, name="library"),
    path("create/", views.create_story, name="create_story"),
    path("story/<int:pk>/chat/", views.story_chat, name="story_chat"),
    path("story/<int:pk>/message/", views.send_message, name="send_message"),
    path("story/<int:pk>/generate/", views.generate_story, name="generate_story"),
    path("story/<int:pk>/approve/", views.approve_story, name="approve_story"),
    path("story/<int:pk>/audio/", views.generate_audio, name="generate_audio"),
    path("story/<int:pk>/", views.story_detail, name="story_detail"),
    path("story/<int:pk>/delete/", views.delete_story, name="delete_story"),
]
