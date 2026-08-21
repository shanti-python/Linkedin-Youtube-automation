"""Groq LLM integration for generating LinkedIn comment replies."""

from dataclasses import dataclass
from typing import Optional

from groq import Groq

from linkedin_comment_bot.app.config import Settings, get_settings
from linkedin_comment_bot.app.utils.helpers import detect_language_simple, is_question
from linkedin_comment_bot.app.utils.logger import get_logger

logger = get_logger()

SYSTEM_PROMPT = """You are the owner of this LinkedIn account.
Reply professionally.
Maximum 60 words.
Be friendly.
Never use emojis unless appropriate.
Never argue.
Never discuss politics.
Never generate offensive language.
Return only the reply."""


@dataclass
class ReplyContext:
    comment: str
    author_name: str
    post_content: Optional[str] = None
    post_topic: Optional[str] = None
    industry: str = "Technology"
    detected_language: str = "en"


@dataclass
class GeneratedReply:
    text: str
    is_question: bool
    sentiment: str
    detected_language: str


class GroqReplyService:
    """Generates contextual LinkedIn comment replies via Groq API."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self._client: Optional[Groq] = None

    @property
    def client(self) -> Groq:
        if self._client is None:
            if not self.settings.groq_api_key:
                raise ValueError("GROQ_API_KEY is not configured")
            self._client = Groq(api_key=self.settings.groq_api_key)
        return self._client

    def _build_system_prompt(self) -> str:
        persona = self.settings.persona
        return f"""{persona}

Instructions:
1. Carefully read and analyze the comment from the user.
2. Understand the specific intent, context, and tone of the comment.
3. If the comment contains only "CFBR", "#CFBR", "#cfbr", "hashtag #cfbr", or minor variations of the "CFBR" (Commenting For Better Reach) abbreviation (case-insensitive, with or without hashtag or spacing), do NOT generate any reply. Instead, you MUST return exactly the text "SKIP_COMMENT" and nothing else.
4. Generate a personalized and context-aware reply that directly matches the intent and addresses the content of the comment. Do NOT post generic, boilerplate, or robotic responses.
5. Reply professionally, naturally, and in a friendly manner.
6. Maximum length: 60 words.
7. Do not use emojis unless they are highly appropriate.
8. Never argue, discuss politics, or generate offensive language.
9. Output ONLY the reply text. Do not include any meta-text, introductions, quotes, or explanations."""

    def _build_user_prompt(self, ctx: ReplyContext) -> str:
        parts = [
            f"Comment by {ctx.author_name}:",
            f'"{ctx.comment}"',
            "",
            f"Industry: {ctx.industry}",
        ]
        if ctx.post_topic:
            parts.extend(["", f"Post Topic: {ctx.post_topic}"])
        if ctx.post_content:
            parts.extend(["", f"Post Content: {ctx.post_content[:500]}"])
        if ctx.detected_language != "en":
            parts.extend([
                "",
                f"Note: Comment appears to be in language code '{ctx.detected_language}'. "
                "Reply in the same language if appropriate.",
            ])
        parts.extend(["", "Generate a personalized, context-aware reply matching the comment's intent (max 60 words). Return only the reply text."])
        return "\n".join(parts)

    def generate_reply(self, ctx: ReplyContext) -> GeneratedReply:
        """Send comment context to Groq and return generated reply."""
        detected_lang = detect_language_simple(ctx.comment)
        ctx.detected_language = detected_lang
        question = is_question(ctx.comment)

        user_prompt = self._build_user_prompt(ctx)
        system_prompt = self._build_system_prompt()
        logger.info("Generating reply for comment by %s", ctx.author_name)

        response = self.client.chat.completions.create(
            model=self.settings.groq_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            max_tokens=150,
        )

        reply_text = (response.choices[0].message.content or "").strip()
        reply_text = reply_text.strip('"').strip("'")

        from linkedin_comment_bot.app.utils.helpers import analyze_sentiment_simple

        return GeneratedReply(
            text=reply_text,
            is_question=question,
            sentiment=analyze_sentiment_simple(ctx.comment),
            detected_language=detected_lang,
        )

    def generate_test_reply(
        self,
        comment: str,
        author_name: str,
        post_content: Optional[str] = None,
        post_topic: Optional[str] = None,
    ) -> GeneratedReply:
        ctx = ReplyContext(
            comment=comment,
            author_name=author_name,
            post_content=post_content,
            post_topic=post_topic,
            industry=self.settings.industry,
        )
        return self.generate_reply(ctx)
