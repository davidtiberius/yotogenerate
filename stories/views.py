import json
import secrets
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

import anthropic
from openai import OpenAI

from .models import ChatMessage, Story, YotoAccount
from . import yoto as yoto_api

SYSTEM_PROMPT = """You are a warm, creative children's story writing assistant. A parent is using you to craft a personalised bedtime or reading story for their child.

You have been given some basic details about the child. Your job is to:

1. Ask 2–3 friendly clarifying questions to make the story as good as possible. Cover:
   - Target age (if not already given) — this determines vocabulary and complexity
   - Preferred story length: short (~300 words), medium (~600 words), or long (~1000 words)
   - The mood/tone: funny and silly, magical and whimsical, exciting adventure, or calm and cosy
   - Whether there's a gentle lesson or theme they'd like woven in (optional)

2. Once you have enough information, tell the parent "I have everything I need — shall I write the story?" and wait for their go-ahead.

3. When asked to write the story, produce a beautifully crafted, age-appropriate children's story that:
   - Features the child as the hero or central character
   - Naturally includes the people, places, and interests they provided
   - Has a clear beginning, middle, and end
   - Ends warmly and satisfyingly
   - Uses age-appropriate language
   - Gives the story a great title on the very first line as: Title: <title here>

4. After writing the story, ask if the parent would like any changes. Be open to revisions — adjusting characters, adding details, changing the ending, etc.

5. When the parent is happy, respond with exactly: "STORY_APPROVED" on its own line, so the system knows to save it.

Always be warm, enthusiastic, and encouraging. This is a special, personalised gift for a child.

Child details:
{child_details}"""


def _build_child_details(story: Story) -> str:
    parts = [f"Name: {story.child_name}"]
    if story.child_age:
        parts.append(f"Age: {story.child_age}")
    if story.interests:
        parts.append(f"Interests: {story.interests}")
    if story.characters:
        parts.append(f"People to include: {story.characters}")
    if story.places:
        parts.append(f"Familiar places: {story.places}")
    if story.extra_notes:
        parts.append(f"Extra notes: {story.extra_notes}")
    return "\n".join(parts)


def _get_ai_response(story: Story, user_message: str | None = None) -> str:
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    history = list(story.messages.all())
    api_messages = [{"role": m.role, "content": m.content} for m in history]

    if user_message:
        api_messages.append({"role": "user", "content": user_message})

    system = SYSTEM_PROMPT.format(child_details=_build_child_details(story))

    response = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=4096,
        system=system,
        messages=api_messages,
    )
    return response.content[0].text


def _extract_title(content: str) -> str:
    for line in content.splitlines():
        if line.strip().lower().startswith("title:"):
            return line.split(":", 1)[1].strip()
    return ""


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
    yoto_connected = YotoAccount.objects.filter(user=request.user).exists()
    return render(request, "stories/library.html", {
        "stories": stories,
        "yoto_connected": yoto_connected,
    })


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

        # Kick off first AI message
        ai_text = _get_ai_response(story)
        ChatMessage.objects.create(story=story, role="assistant", content=ai_text)

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

    ai_text = _get_ai_response(story, user_text)
    ChatMessage.objects.create(story=story, role="assistant", content=ai_text)

    story_approved = "STORY_APPROVED" in ai_text

    return JsonResponse({
        "reply": ai_text,
        "story_approved": story_approved,
    })


@login_required
@require_POST
def generate_story(request, pk):
    story = get_object_or_404(Story, pk=pk, user=request.user)

    instruction = (
        "Please write the full story now based on everything we've discussed. "
        "Remember to put the title on the first line as: Title: <title>"
    )

    ChatMessage.objects.create(story=story, role="user", content=instruction)
    ai_text = _get_ai_response(story, instruction)
    ChatMessage.objects.create(story=story, role="assistant", content=ai_text)

    # Extract title and store story content
    title = _extract_title(ai_text)
    # Strip the title line from stored content
    lines = ai_text.splitlines()
    content_lines = [l for l in lines if not l.strip().lower().startswith("title:")]
    content = "\n".join(content_lines).strip()

    story.content = content
    if title:
        story.title = title
    story.save()

    return JsonResponse({
        "reply": ai_text,
        "title": story.display_title(),
        "content": content,
    })


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

    try:
        client = OpenAI(api_key=settings.OPENAI_API_KEY)

        speech_text = story.content
        if story.title:
            speech_text = f"{story.title}.\n\n{story.content}"

        audio_dir = Path(settings.MEDIA_ROOT) / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        audio_path = audio_dir / f"story_{pk}.mp3"

        with client.audio.speech.with_streaming_response.create(
            model="tts-1-hd",
            voice=story.tts_voice,
            input=speech_text,
        ) as response:
            response.stream_to_file(str(audio_path))

        story.audio_file = f"audio/story_{pk}.mp3"
        story.save()

        return JsonResponse({"audio_url": story.audio_file.url})

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@login_required
def story_detail(request, pk):
    story = get_object_or_404(Story, pk=pk, user=request.user)
    yoto_connected = YotoAccount.objects.filter(user=request.user).exists()
    return render(request, "stories/story_detail.html", {
        "story": story,
        "yoto_connected": yoto_connected,
    })


@login_required
@require_POST
def delete_story(request, pk):
    story = get_object_or_404(Story, pk=pk, user=request.user)
    if story.audio_file:
        audio_path = Path(settings.MEDIA_ROOT) / story.audio_file.name
        if audio_path.exists():
            audio_path.unlink()
    story.delete()
    messages.success(request, "Story deleted.")
    return redirect("library")


# ── Yoto OAuth ──────────────────────────────────────────────────────────────

@login_required
def yoto_connect(request):
    if not settings.YOTO_CLIENT_ID:
        messages.error(request, "Yoto integration is not configured.")
        return redirect("library")

    code_verifier, code_challenge = yoto_api.generate_pkce()
    state = secrets.token_urlsafe(16)

    request.session["yoto_code_verifier"] = code_verifier
    request.session["yoto_state"] = state

    url = yoto_api.build_authorize_url(
        client_id=settings.YOTO_CLIENT_ID,
        redirect_uri=settings.YOTO_REDIRECT_URI,
        state=state,
        code_challenge=code_challenge,
    )
    return redirect(url)


@login_required
def yoto_callback(request):
    error = request.GET.get("error")
    if error:
        messages.error(request, f"Yoto login failed: {request.GET.get('error_description', error)}")
        return redirect("library")

    state = request.GET.get("state")
    if state != request.session.pop("yoto_state", None):
        messages.error(request, "Invalid OAuth state. Please try again.")
        return redirect("library")

    code = request.GET.get("code")
    code_verifier = request.session.pop("yoto_code_verifier", None)
    if not code or not code_verifier:
        messages.error(request, "Missing OAuth code. Please try again.")
        return redirect("library")

    try:
        token_data = yoto_api.exchange_code(
            client_id=settings.YOTO_CLIENT_ID,
            client_secret=settings.YOTO_CLIENT_SECRET,
            code=code,
            code_verifier=code_verifier,
            redirect_uri=settings.YOTO_REDIRECT_URI,
        )
        yoto_api.save_tokens(request.user, token_data)
        messages.success(request, "Your Yoto account is now connected!")
    except Exception as e:
        messages.error(request, f"Failed to connect Yoto account: {e}")

    return redirect("library")


@login_required
@require_POST
def yoto_disconnect(request):
    YotoAccount.objects.filter(user=request.user).delete()
    messages.success(request, "Yoto account disconnected.")
    return redirect("library")


# ── Push to Yoto ─────────────────────────────────────────────────────────────

@login_required
@require_POST
def push_to_yoto(request, pk):
    story = get_object_or_404(Story, pk=pk, user=request.user)

    if not story.audio_file:
        return JsonResponse({"error": "No audio file — generate audio first."}, status=400)

    try:
        yoto_account = request.user.yoto_account
    except YotoAccount.DoesNotExist:
        return JsonResponse({"error": "Yoto account not connected."}, status=400)

    audio_path = Path(settings.MEDIA_ROOT) / story.audio_file.name
    if not audio_path.exists():
        return JsonResponse({"error": "Audio file not found on disk."}, status=400)

    try:
        card = yoto_api.push_audio_to_yoto(yoto_account, str(audio_path), story.display_title())
        card_id = card.get("cardId", "")
        story.yoto_card_id = card_id
        story.save(update_fields=["yoto_card_id"])
        return JsonResponse({"ok": True, "card_id": card_id})
    except yoto_api.YotoTokenExpired as e:
        return JsonResponse({"error": str(e), "reconnect": True}, status=401)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
