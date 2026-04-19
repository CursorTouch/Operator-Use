---
name: youtube-cli
description: Extract information and transcript from a YouTube video given its ID. Use when the agent needs to analyze video content, summarize videos, or retrieve specific details from YouTube URLs.
---

## What This Skill Does
This skill uses `yt-dlp` to fetch metadata (title, uploader, views, duration) and `youtube-transcript-api` to retrieve the closed captions for a given YouTube video.

## Steps
1. Use the script `skills/youtube-cli/scripts/yt_processor.py` via the terminal.
2. Run the command: `python skills/youtube-cli/scripts/yt_processor.py <video_id> [flags]`
3. Available flags: `--title`, `--uploader`, `--duration`, `--views`, `--transcript`, `--description`, `--output` (`-o`).
4. If no flags are specified, it defaults to: `--title`, `--uploader`, `--duration`, `--views`.
5. Run `python skills/youtube-cli/scripts/yt_processor.py --help` for usage details.
6. The script outputs the requested information in JSON format. Use `--output <path>` to save the transcript text or description text directly to a file. Use this to avoid terminal truncation for long transcripts.

## Common Failures
- **400 Bad Request**: Often caused by outdated `pytube` libraries; this skill uses `yt-dlp` as a more robust alternative.
- **No transcript available**: Some videos do not have captions or the transcript service is disabled for that video.
