import os
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Browser config
    CHROME_PROFILE_PATH: Optional[str] = None
    HEADLESS: bool = False
    
    # Human simulation config
    MIN_TYPING_DELAY: float = 0.05
    MAX_TYPING_DELAY: float = 0.15
    MIN_RANDOM_DELAY: float = 15.0
    MAX_RANDOM_DELAY: float = 45.0
    MIN_WATCH_DELAY: float = 6.0
    MAX_WATCH_DELAY: float = 15.0
    AUTO_REWRITE_REPLY: bool = False
    
    # Data storage config
    CSV_PATH: str = "replies/replies_log.csv"
    JSON_PATH: str = "replies/replies_log.json"
    RESUME_PATH: str = "replies/resume_state.json"
    
    # LLM Settings
    LLM_PROVIDER: str = "openai"  # openai, groq, ollama
    OPENAI_API_KEY: Optional[str] = None
    GROQ_API_KEY: Optional[str] = None
    OLLAMA_API_URL: str = "http://localhost:11434/v1"
    LLM_MODEL: str = "gpt-4o-mini"  # e.g., llama3-8b-8192 for Groq, llama3 for Ollama
    
    # Limits
    MAX_REPLIES_PER_RUN: int = 50
    
    # Google Sheets (Optional)
    GOOGLE_SHEET_ID: Optional[str] = None
    GOOGLE_SHEET_CREDENTIALS_FILE: Optional[str] = None
    GOOGLE_SHEET_RULES_URL: Optional[str] = None
    
    # App root path resolver
    BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent
    
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parent.parent.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    def get_absolute_path(self, relative_path: str) -> str:
        """Helper to resolve paths relative to base directory."""
        path = Path(relative_path)
        if path.is_absolute():
            return str(path)
        return str(self.BASE_DIR / path)

settings = Settings()

# Ensure directories exist
os.makedirs(settings.get_absolute_path("logs"), exist_ok=True)
os.makedirs(settings.get_absolute_path("replies"), exist_ok=True)
