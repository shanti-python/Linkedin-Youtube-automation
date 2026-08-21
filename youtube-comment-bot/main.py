import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from youtube_reply_bot.app.router.youtube_router import router as youtube_router
from youtube_reply_bot.app.utils.logger import logger
from youtube_reply_bot.app.utils.browser import browser_manager

app = FastAPI(
    title="YouTube Comment Auto Reply Bot API",
    description="A production-ready FastAPI system that automates YouTube Studio comment replies using Playwright and OpenAI/Groq/Ollama.",
    version="1.0.0"
)

# Enable CORS for frontend clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include the routers
app.include_router(youtube_router)

@app.on_event("startup")
async def startup_event():
    logger.info("Starting YouTube Comment Auto Reply Bot Service...")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Stopping YouTube Comment Auto Reply Bot Service...")
    # Safe cleanup of browser instances
    await browser_manager.close_browser()

@app.get("/")
async def root():
    """Welcome endpoint showing API details."""
    return {
        "message": "Welcome to the YouTube Comment Auto Reply Bot API",
        "documentation": "/docs",
        "status_endpoint": "/youtube/status"
    }

if __name__ == "__main__":
    logger.info("Starting Uvicorn server on http://127.0.0.1:8000")
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
