from django.db import models
from django.contrib.auth.models import User


class Story(models.Model):
    VOICE_CHOICES = [
        ("alloy", "Alloy (Neutral)"),
        ("echo", "Echo (Male)"),
        ("fable", "Fable (Storyteller)"),
        ("nova", "Nova (Female, Warm)"),
        ("shimmer", "Shimmer (Female, Bright)"),
        ("onyx", "Onyx (Male, Deep)"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="stories")
    title = models.CharField(max_length=200, blank=True)
    child_name = models.CharField(max_length=100)
    child_age = models.CharField(max_length=50, blank=True)
    interests = models.TextField(blank=True, help_text="Comma-separated list of interests")
    characters = models.TextField(blank=True, help_text="People to include (names and relationship)")
    places = models.TextField(blank=True, help_text="Familiar places to include")
    extra_notes = models.TextField(blank=True, help_text="Any other details")
    content = models.TextField(blank=True)
    tts_voice = models.CharField(max_length=20, choices=VOICE_CHOICES, default="fable")
    audio_file = models.FileField(upload_to="audio/", blank=True, null=True)
    is_complete = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title or f"{self.child_name}'s Story"

    def display_title(self):
        return self.title or f"{self.child_name}'s Story"


class ChatMessage(models.Model):
    ROLE_USER = "user"
    ROLE_ASSISTANT = "assistant"
    ROLE_CHOICES = [(ROLE_USER, "User"), (ROLE_ASSISTANT, "Assistant")]

    story = models.ForeignKey(Story, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.role}: {self.content[:50]}"
