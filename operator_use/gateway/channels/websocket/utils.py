from __future__ import annotations

# Client → server message types
MSG_TYPE_MESSAGE = 'message'

# Server → client message types
MSG_TYPE_START = 'start'
MSG_TYPE_CHUNK = 'chunk'
MSG_TYPE_END = 'end'
MSG_TYPE_DONE = 'done'
MSG_TYPE_ERROR = 'error'
MSG_TYPE_REACT = 'react'    # {"type": "react", "message_id": "...", "emoji": "..."}
MSG_TYPE_FILE = 'file'      # {"type": "file", "filename": "...", "mime_type": "...", "data": "<b64>", "caption": "...", "reply_to": "..."|null}
