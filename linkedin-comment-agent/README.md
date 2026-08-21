# LinkedIn Comment Auto Reply Bot

Production-ready LinkedIn comment monitoring and auto-reply system using Python, Playwright, FastAPI, and Groq.

## Features

- Automated LinkedIn login using saved email and password
- Persistent session & cookie storage (`exports/cookies.json`) so repeated logins are not required
- Full comment extraction with nested reply support
- Groq LLM-powered contextual replies
- Human-like typing, scrolling, and rate limiting
- CSV logging, JSON export, optional Google Sheets
- Resume support via `processed_comments.json`
- Multi-post batch processing
- Spam, bot, and offensive comment filtering

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
# Edit .env with your GROQ_API_KEY, LINKEDIN_EMAIL, and LINKEDIN_PASSWORD
```

### Automated Login & Cookie Persistence

1. Set `LINKEDIN_EMAIL` and `LINKEDIN_PASSWORD` in `.env`.
2. The bot will automatically log into LinkedIn on first run and save session cookies to `exports/cookies.json`.
3. Subsequent runs automatically restore and reuse saved cookies without requiring re-authentication.

## Run

### Option 1: Scan All Account Posts (Default)
```bash
# Automatically logs in / restores cookies, scans all posts on your account, and replies to comments
python run_bot.py
```

### Option 2: Scan a Specific Post
```bash
python run_bot.py --post-url "https://www.linkedin.com/posts/example-post-12345"
```

### Option 3: FastAPI Server
```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/linkedin/account/reply` | Scan all posts on the logged-in account and reply to comments |
| POST | `/linkedin/comment/reply` | Reply to comments on a specific post |
| POST | `/linkedin/comment/reply/groq` | Same, Groq mode explicit |
| POST | `/linkedin/comment/reply/test` | Test mode (no Groq API) |
| POST | `/linkedin/comment/reply/batch` | Process multiple posts |
| POST | `/linkedin/comment/groq/test` | Test Groq reply without browser |
| GET | `/linkedin/comment/status` | Processing status |
| GET | `/health` | Health check |

### Example request

```bash
curl -X POST http://localhost:8000/linkedin/comment/reply \
  -H "Content-Type: application/json" \
  -d '{
    "post_url": "https://www.linkedin.com/posts/activity-1234567890",
    "mode": "groq"
  }'
```

### Example response

```json
{
  "status": "success",
  "total_comments": 54,
  "replied": 36,
  "skipped": 18,
  "errors": 0
}
```

## Project Structure

```
linkedin_comment_bot/
  app/
    router/linkedin_router.py
    services/
      linkedin_service.py
      comment_reader.py
      groq_reply_service.py
      comment_reply_service.py
    models/
    utils/
    config.py
logs/
exports/
main.py
requirements.txt
```

## Important Notes

- LinkedIn DOM selectors change frequently; the service uses multiple fallback selectors.
- Use responsibly and comply with LinkedIn's Terms of Service.
- Set `LINKEDIN_ACCOUNT_NAME` for accurate already-replied detection.
