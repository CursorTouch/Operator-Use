---
name: youtube-cli
description: Search for videos, extract metadata, retrieve transcripts, and fetch comments from YouTube. Use when analyzing video content for research, summarizing long-form content, or gathering information from YouTube URLs.
---

## What This Skill Does
This skill uses `yt-dlp` to fetch metadata (title, uploader, views, duration) and `youtube-transcript-api` to retrieve the closed captions for a given YouTube video. It also includes search functionality using `youtube-search-python`.

## Search Videos
1. Use the script `skills/youtube-cli/scripts/search.py`.
2. Run the command: `python skills/youtube-cli/scripts/search.py "<query>" [flags]`
3. Available flags: `--limit <N>`, `--language <code>`, `--region <code>`, `--output` (`-o`).
4. The script outputs the top search results in JSON format. Use `--output <path>` to save the results to a file.

## Steps
1. Use the script `skills/youtube-cli/scripts/search.py` via the terminal. Run `python skills/youtube-cli/scripts/search.py --help` for usage details.
2. Use the script `skills/youtube-cli/scripts/video.py` via the terminal. Run `python skills/youtube-cli/scripts/video.py --help` for usage details.
3. For long transcripts, descriptions, or comment threads, always use the `--output` flag to save the data to a file rather than printing to the terminal to avoid truncation.
4. If a video is part of a series or you need to process multiple videos, chain these calls as necessary.

## Common Failures
- **400 Bad Request**: Often caused by outdated `yt-dlp` or dependency issues.
- **No transcript available**: Some videos do not have captions, or the transcript service is disabled.
- **Terminal Truncation**: For long outputs, always use the `--output` flag.

## Pro-Tips for Research
- When researching, first search for the topic using `search.py` to identify the most relevant videos.
- Use `video.py` on the top results to extract metadata and assess relevance before extracting full transcripts.
- If you need to analyze a large number of videos, script the interaction using the terminal to batch process them.
