import json

from pathlib import Path

from django.conf import settings as django_settings
from celery.result import AsyncResult
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import ChatMessage, Story
from .tasks import (
    task_generate_audio,
    task_generate_story,
    task_initial_ai_message,
    task_send_message,
)


def home(request):
    if request.user.is_authenticated:
        return redirect("library")
    return render(request, "stories/home.html")


def register(request):
    if request.user.is_authenticated:
        return redirect("library")
    if request.method == "POST":
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect("library")
    else:
        form = UserCreationForm()
    return render(request, "registration/register.html", {"form": form})


@login_required
def library(request):
    stories = Story.objects.filter(user=request.user)
    return render(request, "stories/library.html", {"stories": stories})


@login_required
def create_story(request):
    if request.method == "POST":
        story = Story.objects.create(
            user=request.user,
            child_name=request.POST.get("child_name", "").strip(),
            child_age=request.POST.get("child_age", "").strip(),
            interests=request.POST.get("interests", "").strip(),
            characters=request.POST.get("characters", "").strip(),
            places=request.POST.get("places", "").strip(),
            extra_notes=request.POST.get("extra_notes", "").strip(),
            tts_voice=request.POST.get("tts_voice", "fable"),
        )

        task_initial_ai_message.delay(story.pk)

        return redirect("story_chat", pk=story.pk)

    return render(request, "stories/create.html", {"voice_choices": Story.VOICE_CHOICES})


@login_required
def story_chat(request, pk):
    story = get_object_or_404(Story, pk=pk, user=request.user)
    chat_messages = story.messages.all()
    return render(request, "stories/chat.html", {
        "story": story,
        "chat_messages": chat_messages,
    })


@login_required
@require_POST
def send_message(request, pk):
    story = get_object_or_404(Story, pk=pk, user=request.user)

    try:
        data = json.loads(request.body)
        user_text = data.get("message", "").strip()
    except (json.JSONDecodeError, KeyError):
        return JsonResponse({"error": "Invalid request"}, status=400)

    if not user_text:
        return JsonResponse({"error": "Empty message"}, status=400)

    ChatMessage.objects.create(story=story, role="user", content=user_text)

    result = task_send_message.delay(story.pk, user_text)

    return JsonResponse({"task_id": result.id})


@login_required
@require_POST
def generate_story(request, pk):
    story = get_object_or_404(Story, pk=pk, user=request.user)
    result = task_generate_story.delay(story.pk)
    return JsonResponse({"task_id": result.id})


@login_required
@require_POST
def approve_story(request, pk):
    story = get_object_or_404(Story, pk=pk, user=request.user)
    story.is_complete = True
    story.save()
    return JsonResponse({"ok": True, "redirect": f"/story/{pk}/"})


@login_required
@require_POST
def generate_audio(request, pk):
    story = get_object_or_404(Story, pk=pk, user=request.user)

    if not story.content:
        return JsonResponse({"error": "No story content to convert"}, status=400)

    result = task_generate_audio.delay(story.pk)
    return JsonResponse({"task_id": result.id})


@login_required
def task_status(request, task_id):
    result = AsyncResult(task_id)

    if result.ready():
        if result.successful():
            return JsonResponse({"status": "done", "result": result.result})
        else:
            return JsonResponse({"status": "error", "error": str(result.result)})

    return JsonResponse({"status": "pending"})


@login_required
def story_detail(request, pk):
    story = get_object_or_404(Story, pk=pk, user=request.user)
    return render(request, "stories/story_detail.html", {"story": story})


@login_required
@require_POST
def delete_story(request, pk):
    story = get_object_or_404(Story, pk=pk, user=request.user)
    if story.audio_file:
        audio_path = Path(django_settings.MEDIA_ROOT) / story.audio_file.name
        if audio_path.exists():
            audio_path.unlink()
    story.delete()
    messages.success(request, "Story deleted.")
    return redirect("library")
