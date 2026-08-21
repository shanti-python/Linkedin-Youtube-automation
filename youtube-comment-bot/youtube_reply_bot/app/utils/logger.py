import logging
import os
from logging.handlers import RotatingFileHandler
from youtube_reply_bot.app.config import settings

def setup_logger(name: str = "youtube_reply_bot") -> logging.Logger:
    """Sets up a rotating file logger and a stream logger for console output."""
    logger = logging.getLogger(name)
    
    # Avoid duplicate handlers if already configured
    if logger.handlers:
        return logger
        
    logger.setLevel(logging.INFO)
    
    log_format = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s"
    )
    
    # Console Handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(log_format)
    logger.addHandler(console_handler)
    
    # Rotating File Handler
    log_file_path = settings.get_absolute_path(os.path.join("logs", "bot.log"))
    file_handler = RotatingFileHandler(
        log_file_path, maxBytes=5 * 1024 * 1024, backupCount=3
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(log_format)
    logger.addHandler(file_handler)
    
    return logger

logger = setup_logger()
