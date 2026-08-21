import re
from urllib.parse import urlparse, parse_qs
from typing import Optional

def extract_video_id(url: str) -> Optional[str]:
    """
    Extracts the YouTube Video ID from various YouTube URL structures.
    
    Supported formats:
    - https://www.youtube.com/watch?v=VIDEO_ID
    - https://youtube.com/watch?v=VIDEO_ID
    - https://youtu.be/VIDEO_ID
    - https://m.youtube.com/watch?v=VIDEO_ID
    - https://youtube.com/embed/VIDEO_ID
    - https://youtube.com/v/VIDEO_ID
    - https://studio.youtube.com/video/VIDEO_ID/comments
    """
    if not url:
        return None
        
    url = url.strip()
    
    # Handle studio.youtube.com links
    studio_match = re.search(r"studio\.youtube\.com/video/([^/]+)/comments", url)
    if studio_match:
        return studio_match.group(1)
        
    # Handle /shorts/ links
    shorts_match = re.search(r"/shorts/([^/?#&]+)", url)
    if shorts_match:
        return shorts_match.group(1)
        
    # Handle standard watch links
    parsed_url = urlparse(url)
    if parsed_url.hostname in ("www.youtube.com", "youtube.com", "m.youtube.com"):
        query = parse_qs(parsed_url.query)
        if "v" in query:
            return query["v"][0]
            
    # Handle youtu.be short urls
    if parsed_url.hostname == "youtu.be":
        return parsed_url.path.lstrip("/")
        
    # Handle embed/v paths
    path_parts = parsed_url.path.split("/")
    if len(path_parts) > 2 and path_parts[1] in ("embed", "v"):
        return path_parts[2]
        
    # General regex fallback for any 11-char ID
    # YouTube video IDs are typically 11 alphanumeric characters, hyphen or underscore
    id_match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})(?:\?|&|$|\/)", url)
    if id_match:
        return id_match.group(1)
        
    # If the URL is just an 11-character string, assume it's the ID
    if len(url) == 11 and re.match(r"^[0-9A-Za-z_-]{11}$", url):
        return url
        
    return None
