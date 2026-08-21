import re
import csv
import httpx
from typing import Tuple, Optional, Dict
from openai import AsyncOpenAI
from youtube_reply_bot.app.config import settings
from youtube_reply_bot.app.utils.logger import logger
from youtube_reply_bot.app.utils.helpers import extract_video_id

class ReplyGenerator:
    def __init__(self):
        self.predefined_keywords = {
            "guide": "Thank you! I'll make a detailed guide soon.",
            "tutorial": "Thank you! I'll make a detailed guide soon.",
            "python": "Python tutorial is coming soon.",
            "thanks": "You're welcome 😊",
            "thank you": "You're welcome 😊",
            "nice": "Thanks for watching!",
            "awesome": "Thanks for watching!",
            "great": "Thanks for watching!",
        }
        self.default_reply = "Thank you for your support."
        
        # Dynamic rules fetched from Google Sheet: {video_id: {keyword: reply_text}}
        self.sheet_rules: Dict[str, Dict[str, str]] = {}

        # Setup client based on provider
        self.client = None
        self._init_llm_client()

    async def fetch_sheet_rules(self) -> None:
        """Fetches the auto-reply rules CSV dynamically from the public Google Sheet."""
        if not settings.GOOGLE_SHEET_RULES_URL:
            logger.info("No GOOGLE_SHEET_RULES_URL configured. Using default local rules.")
            return

        # Convert Google Sheet URL to direct CSV export URL
        url = settings.GOOGLE_SHEET_RULES_URL
        gid = None
        if "gid=" in url:
            m = re.search(r"[?#&]gid=(\d+)", url)
            if m:
                gid = m.group(1)

        if "/edit" in url:
            url = url.split("/edit")[0] + "/export?format=csv"
            if gid:
                url += f"&gid={gid}"

        logger.info(f"Fetching latest auto-reply rules from Google Sheet: {url}")
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                response = await client.get(url)
                if response.status_code != 200:
                    logger.error(f"Failed to fetch Google Sheet rules: HTTP {response.status_code}")
                    return

                csv_data = response.text
                reader = csv.reader(csv_data.splitlines())
                header = next(reader, None)
                if not header:
                    logger.warning("Google Sheet rules CSV is empty.")
                    return

                col_indices = {col.strip().lower(): idx for idx, col in enumerate(header)}
                video_idx = next((idx for name, idx in col_indices.items() if name in ("video url", "url", "video_url") or "url" in name), 1)
                comment_idx = next((idx for name, idx in col_indices.items() if name in ("comment text (keyword)", "comment text", "keyword", "user_comment", "user comment", "comment") or "keyword" in name or "comment" in name), 2)
                reply_idx = next((idx for name, idx in col_indices.items() if name in ("reply message", "reply", "message", "reply_text") or "reply" in name or "message" in name), 3)

                rules = {}
                for row_num, row in enumerate(reader, start=2):
                    if len(row) <= max(video_idx, comment_idx, reply_idx):
                        continue
                    
                    video_url = row[video_idx].strip()
                    user_comment = row[comment_idx].strip().lower()
                    reply_text = row[reply_idx].strip()

                    if not video_url or not user_comment or not reply_text:
                        continue

                    vid_id = extract_video_id(video_url)
                    if not vid_id:
                        logger.warning(f"Could not extract Video ID from URL: {video_url} at row {row_num}")
                        continue

                    if vid_id not in rules:
                        rules[vid_id] = {}
                    
                    rules[vid_id][user_comment] = reply_text

                self.sheet_rules = rules
                logger.info(f"Loaded rules for {len(rules)} videos from Google Sheet.")
                
        except Exception as e:
            logger.error(f"Failed to fetch/parse Google Sheet rules: {str(e)}")

    def _init_llm_client(self):
        """Initializes the OpenAI-compatible client based on the provider settings."""
        provider = settings.LLM_PROVIDER.lower()
        api_key = None
        base_url = None

        try:
            if provider == "openai":
                api_key = settings.OPENAI_API_KEY
                # Uses default OpenAI endpoint
            elif provider == "groq":
                api_key = settings.GROQ_API_KEY
                base_url = "https://api.groq.com/openai/v1"
            elif provider == "ollama":
                api_key = "ollama"  # Ollama doesn't require a real API key
                base_url = settings.OLLAMA_API_URL
                
            if provider in ("openai", "groq") and not api_key:
                logger.warning(f"{settings.LLM_PROVIDER.upper()} selected, but API key is missing. AI replies will fall back to predefined responses.")
                return

            self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
            logger.info(f"Initialized LLM client for provider: {provider} using model: {settings.LLM_MODEL}")
        except Exception as e:
            logger.error(f"Failed to initialize LLM client: {str(e)}")
            self.client = None

    def detect_sentiment_and_type(self, text: str) -> Tuple[str, bool, bool]:
        """
        Analyzes comment text for:
        - Sentiment (positive, neutral, negative)
        - Is Question (True/False)
        - Is Offensive/Spam (True/False)
        """
        text_lower = text.lower()
        
        # Check for offensive/spam content using exact word boundaries
        offensive_keywords = [
            "abuse", "idiot", "stupid", "fuck", "shit", "bitch", "scam", 
            "fake", "crap", "bastard", "asshole", "hate you"
        ]
        is_offensive = any(re.search(r'\b' + re.escape(word) + r'\b', text_lower) for word in offensive_keywords)
        
        # Check if it is a question
        is_question = "?" in text or any(text_lower.startswith(word) for word in [
            "how", "why", "what", "when", "where", "who", "can you", "is there", "do you", "please"
        ])
        
        # Determine sentiment
        positive_words = ["love", "great", "awesome", "nice", "thanks", "thank", "good", "perfect", "helpful", "cool", "best"]
        negative_words = ["bad", "worst", "waste", "boring", "useless", "wrong", "fail", "slow", "annoying", "hate"]
        
        pos_score = sum(1 for word in positive_words if word in text_lower)
        neg_score = sum(1 for word in negative_words if word in text_lower)
        
        if is_offensive:
            sentiment = "negative"
        elif pos_score > neg_score:
            sentiment = "positive"
        elif neg_score > pos_score:
            sentiment = "negative"
        else:
            sentiment = "neutral"
            
        return sentiment, is_question, is_offensive

    def generate_predefined_reply(self, text: str, video_id: Optional[str] = None) -> str:
        """Generates a reply based on predefined keyword matching (checks Google Sheet rules first, then local)."""
        text_lower = text.lower()
        
        # 1. Try Google Sheet rules for this specific video
        if video_id and video_id in self.sheet_rules:
            video_rules = self.sheet_rules[video_id]
            for keyword, response in video_rules.items():
                if keyword in text_lower:
                    logger.info(f"Matched Google Sheet rule: Keyword '{keyword}' -> Reply '{response[:30]}...'")
                    return response
        
        # 2. Try default local keyword rules
        for keyword, response in self.predefined_keywords.items():
            if keyword in text_lower:
                logger.info(f"Matched default local rule: Keyword '{keyword}' -> Reply '{response[:30]}...'")
                return response
                
        return self.default_reply

    async def generate_ai_reply(self, text: str, video_id: Optional[str] = None) -> str:
        """Generates a reply using LLM (OpenAI, Groq, or Ollama) with error fallback."""
        if not self.client:
            logger.warning("LLM client not configured. Falling back to predefined matching.")
            return self.generate_predefined_reply(text, video_id)

        system_prompt = (
            "You are the owner of this YouTube channel.\n"
            "Reply professionally and politely to the following comment.\n"
            "Constraints:\n"
            "- Maximum 40 words.\n"
            "- Never argue.\n"
            "- Never use offensive language.\n"
            "- Keep replies friendly and support-oriented.\n"
            "- Reply in the same language as the commenter's comment (e.g. if the comment is in Spanish, reply in Spanish).\n"
            "- Return ONLY the direct reply text, with no wrapping quotes, no introduction, and no extra text."
        )

        try:
            response = await self.client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text}
                ],
                max_tokens=100,
                temperature=0.7
            )
            reply = response.choices[0].message.content.strip()
            
            # Strip outer quotes if the LLM returned them despite instructions
            if reply.startswith('"') and reply.endswith('"'):
                reply = reply[1:-1].strip()
            if reply.startswith("'") and reply.endswith("'"):
                reply = reply[1:-1].strip()
                
            return reply
        except Exception as e:
            logger.error(f"Error during LLM reply generation: {str(e)}. Falling back to predefined reply.")
            return self.generate_predefined_reply(text, video_id)

    def resolve_spintax(self, text: str) -> str:
        """Resolves spintax formatted text like {Thanks|Thank you}."""
        pattern = re.compile(r'\{([^{}]+)\}')
        import random
        while True:
            match = pattern.search(text)
            if not match:
                break
            choices = match.group(1).split('|')
            text = text.replace(match.group(0), random.choice(choices), 1)
        return text

    def humanize_reply_rules(self, reply_text: str, author_handle: str) -> str:
        """
        Light humanization only (spintax + minor punctuation).
        Do NOT auto-add @mentions or emoji spam — YouTube often holds those
        replies as author-only / likely-spam when posted by automation.
        """
        import random
        reply = self.resolve_spintax(reply_text)

        # Keep replies plain; strip accidental leading @handles from templates
        reply = re.sub(r"^@\S+\s+", "", reply).strip()

        if reply.endswith(".") and random.random() < 0.3:
            reply = reply[:-1] + "!"

        return reply.strip()

    async def rewrite_reply_with_llm(self, comment_text: str, reply_template: str) -> str:
        """Uses LLM to rewrite/rephrase the reply to sound like a human response to the comment."""
        if not self.client:
            return reply_template

        system_prompt = (
            "You are a YouTube channel owner replying to a comment.\n"
            "You want to convey this message (the reply template):\n"
            f"'{reply_template}'\n\n"
            "Rewrite this reply template so that it sounds extremely natural, conversational, and tailored "
            "as a direct response to the user's comment, while keeping the core message/intent of the template completely intact.\n"
            "Constraints:\n"
            "- Do NOT output the template verbatim. Rephrase it uniquely.\n"
            "- Never say something like 'Thanks for the comment' unless the template implies it.\n"
            "- Do not include any HTML, quotes, or metadata in your output.\n"
            "- Return ONLY the rephrased reply text. Maximum 35 words."
        )

        try:
            response = await self.client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"User's comment: {comment_text}"}
                ],
                max_tokens=80,
                temperature=0.8
            )
            reply = response.choices[0].message.content.strip()
            if reply.startswith('"') and reply.endswith('"'):
                reply = reply[1:-1].strip()
            if reply.startswith("'") and reply.endswith("'"):
                reply = reply[1:-1].strip()
            return reply
        except Exception as e:
            logger.error(f"Error rewriting reply with LLM: {str(e)}")
            return reply_template

    async def humanize_static_reply(self, comment_text: str, reply_template: str, author_handle: str) -> str:
        """Orchestrates humanization of a static/predefined reply template."""
        if settings.AUTO_REWRITE_REPLY and self.client:
            logger.info("Auto-rewriting static reply with LLM...")
            rewritten = await self.rewrite_reply_with_llm(comment_text, reply_template)
            if rewritten and rewritten != reply_template:
                return self.resolve_spintax(rewritten)
        
        # Rule-based fallback
        logger.info("Applying rule-based humanization to static reply...")
        return self.humanize_reply_rules(reply_template, author_handle)

    async def generate_reply(self, text: str, mode: str = "predefined", video_id: Optional[str] = None) -> str:
        """Wrapper method to generate replies based on the selected mode."""
        if mode == "ai":
            return await self.generate_ai_reply(text, video_id)
        return self.generate_predefined_reply(text, video_id)

# Global instance of ReplyGenerator
reply_generator = ReplyGenerator()
