import re

def ensure_directory(path:str):
    path.mkdir(parents=True, exist_ok=True)

def strip_json_comments(text: str) -> str:
    """Strip // and /* */ comments from a JSON string."""
    text = re.sub(r'//.*', '', text)
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    return text