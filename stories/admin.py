from django.contrib import admin
from .models import Story, ChatMessage


@admin.register(Story)
class StoryAdmin(admin.ModelAdmin):
    list_display = ["display_title", "child_name", "user", "is_complete", "created_at"]
    list_filter = ["is_complete", "created_at"]
    search_fields = ["child_name", "title", "user__username"]


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ["story", "role", "created_at"]
    list_filter = ["role"]
