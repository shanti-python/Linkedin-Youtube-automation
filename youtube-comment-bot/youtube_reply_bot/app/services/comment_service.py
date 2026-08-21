import os
import csv
import json
from datetime import datetime
from typing import List, Dict, Any, Set, Optional
from youtube_reply_bot.app.config import settings
from youtube_reply_bot.app.utils.logger import logger

class CommentService:
    def __init__(self):
        self.csv_path = settings.get_absolute_path(settings.CSV_PATH)
        self.json_path = settings.get_absolute_path(settings.JSON_PATH)
        self.resume_path = settings.get_absolute_path(settings.RESUME_PATH)
        
        # Ensure directories exist
        os.makedirs(os.path.dirname(self.csv_path), exist_ok=True)
        os.makedirs(os.path.dirname(self.json_path), exist_ok=True)
        os.makedirs(os.path.dirname(self.resume_path), exist_ok=True)
        
        # Initialize files if they don't exist
        self._init_csv()
        self._init_json()
        
        # Load resume state
        self.processed_comments: Dict[str, Set[str]] = self._load_resume_state()

    def _init_csv(self) -> None:
        """Creates the CSV file with headers if it doesn't exist."""
        if not os.path.exists(self.csv_path):
            try:
                with open(self.csv_path, mode="w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        "Video ID", "Comment ID", "Author", "Comment", 
                        "Generated Reply", "Status", "Timestamp", "Reason"
                    ])
                logger.info(f"Initialized CSV log file at {self.csv_path}")
            except Exception as e:
                logger.error(f"Failed to initialize CSV log file: {str(e)}")

    def _init_json(self) -> None:
        """Creates the JSON file with empty array if it doesn't exist."""
        if not os.path.exists(self.json_path):
            try:
                with open(self.json_path, mode="w", encoding="utf-8") as f:
                    json.dump([], f)
                logger.info(f"Initialized JSON log file at {self.json_path}")
            except Exception as e:
                logger.error(f"Failed to initialize JSON log file: {str(e)}")

    def _load_resume_state(self) -> Dict[str, Set[str]]:
        """Loads processed comment IDs from the resume JSON state file."""
        if os.path.exists(self.resume_path):
            try:
                with open(self.resume_path, mode="r", encoding="utf-8") as f:
                    data = json.load(f)
                    # Convert list of IDs back to set for efficient lookup
                    return {vid: set(ids) for vid, ids in data.items()}
            except Exception as e:
                logger.error(f"Failed to load resume state: {str(e)}")
        return {}

    def _save_resume_state(self) -> None:
        """Saves current processed comments registry to the resume JSON file."""
        try:
            # Convert sets to lists for JSON serialization
            data = {vid: list(ids) for vid, ids in self.processed_comments.items()}
            with open(self.resume_path, mode="w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to save resume state: {str(e)}")

    def is_processed(self, video_id: str, comment_id: str) -> bool:
        """Checks if a specific comment ID under a Video ID has already been successfully replied to."""
        return comment_id in self.processed_comments.get(video_id, set())

    def mark_processed(self, video_id: str, comment_id: str) -> None:
        """Marks a comment ID as processed and saves state."""
        if video_id not in self.processed_comments:
            self.processed_comments[video_id] = set()
        self.processed_comments[video_id].add(comment_id)
        self._save_resume_state()

    def save_log(
        self,
        video_id: str,
        comment_id: str,
        author: str,
        comment: str,
        generated_reply: Optional[str],
        status: str,
        reason: Optional[str] = None
    ) -> None:
        """Saves the log entry to CSV, JSON, and appends to Google Sheets (if configured)."""
        timestamp = datetime.now().isoformat()
        
        # 1. Append to CSV
        try:
            with open(self.csv_path, mode="a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    video_id, comment_id, author, comment, 
                    generated_reply or "", status, timestamp, reason or ""
                ])
        except Exception as e:
            logger.error(f"Failed to write to CSV log: {str(e)}")
            
        # 2. Append to JSON
        try:
            log_entry = {
                "video_id": video_id,
                "comment_id": comment_id,
                "author": author,
                "comment": comment,
                "generated_reply": generated_reply,
                "status": status,
                "timestamp": timestamp,
                "reason": reason
            }
            
            # Read existing, append, write back (using small locking or simple load)
            logs = []
            if os.path.exists(self.json_path):
                with open(self.json_path, mode="r", encoding="utf-8") as f:
                    try:
                        logs = json.load(f)
                    except json.JSONDecodeError:
                        logs = []
                        
            logs.append(log_entry)
            with open(self.json_path, mode="w", encoding="utf-8") as f:
                json.dump(logs, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to write to JSON log: {str(e)}")

        # 3. Mark processed if successful
        if status == "replied":
            self.mark_processed(video_id, comment_id)

        # 4. Google Sheets (Optional)
        if settings.GOOGLE_SHEET_ID and settings.GOOGLE_SHEET_CREDENTIALS_FILE:
            self._append_to_google_sheet([
                video_id, comment_id, author, comment,
                generated_reply or "", status, timestamp, reason or ""
            ])

    def _append_to_google_sheet(self, row_data: List[Any]) -> None:
        """Appends a row to the configured Google Sheet using service account credentials."""
        credentials_path = settings.get_absolute_path(settings.GOOGLE_SHEET_CREDENTIALS_FILE)
        if not os.path.exists(credentials_path):
            logger.warning(f"Google Sheet credentials file not found at: {credentials_path}. Skipping sheet append.")
            return

        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
            
            scopes = ["https://www.googleapis.com/auth/spreadsheets"]
            creds = service_account.Credentials.from_service_account_file(
                credentials_path, scopes=scopes
            )
            service = build("sheets", "v4", credentials=creds)
            sheet = service.spreadsheets()
            
            # Append data to the sheet (default to Sheet1 or first sheet)
            body = {
                "values": [row_data]
            }
            sheet.values().append(
                spreadsheetId=settings.GOOGLE_SHEET_ID,
                range="A1",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body=body
            ).execute()
            
            logger.info("Successfully logged entry to Google Sheets.")
        except Exception as e:
            logger.error(f"Failed to append log to Google Sheet: {str(e)}")

# Global instance of CommentService
comment_service = CommentService()
