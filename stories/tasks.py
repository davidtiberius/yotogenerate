from pathlib import Path

import anthropic
from celery import shared_task
from django.conf import settings
from openai import OpenAI

from .models import ChatMessage, Story

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

    if not api_messages:
        api_messages.append({"role": "user", "content": "Hi! I'd like to create a story for my child. Here are the details I've provided so far — please ask me any clarifying questions you need."})

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


@shared_task
def task_initial_ai_message(story_id: int) -> dict:
    story = Story.objects.get(pk=story_id)
    ai_text = _get_ai_response(story)
    ChatMessage.objects.create(story=story, role="assistant", content=ai_text)
    return {"reply": ai_text}


@shared_task
def task_send_message(story_id: int, user_text: str) -> dict:
    story = Story.objects.get(pk=story_id)
    ai_text = _get_ai_response(story, user_text)
    ChatMessage.objects.create(story=story, role="assistant", content=ai_text)
    story_approved = "STORY_APPROVED" in ai_text
    return {"reply": ai_text, "story_approved": story_approved}


@shared_task
def task_generate_story(story_id: int) -> dict:
    story = Story.objects.get(pk=story_id)

    instruction = (
        "Please write the full story now based on everything we've discussed. "
        "Remember to put the title on the first line as: Title: <title>"
    )

    ChatMessage.objects.create(story=story, role="user", content=instruction)
    ai_text = _get_ai_response(story, instruction)
    ChatMessage.objects.create(story=story, role="assistant", content=ai_text)

    title = _extract_title(ai_text)
    lines = ai_text.splitlines()
    content_lines = [l for l in lines if not l.strip().lower().startswith("title:")]
    content = "\n".join(content_lines).strip()

    story.content = content
    if title:
        story.title = title
    story.save()

    return {
        "reply": ai_text,
        "title": story.display_title(),
        "content": content,
    }


@shared_task
def task_generate_audio(story_id: int) -> dict:
    story = Story.objects.get(pk=story_id)

    if not story.content:
        return {"error": "No story content to convert"}

    client = OpenAI(api_key=settings.OPENAI_API_KEY)

    speech_text = story.content
    if story.title:
        speech_text = f"{story.title}.\n\n{story.content}"

    audio_dir = Path(settings.MEDIA_ROOT) / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_path = audio_dir / f"story_{story_id}.mp3"

    with client.audio.speech.with_streaming_response.create(
        model="tts-1-hd",
        voice=story.tts_voice,
        input=speech_text,
    ) as response:
        response.stream_to_file(str(audio_path))

    story.audio_file = f"audio/story_{story_id}.mp3"
    story.save()

    return {"audio_url": story.audio_file.url}
