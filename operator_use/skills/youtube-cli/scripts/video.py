import argparse
import json
import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi


def fmt_duration(seconds):
    if not seconds:
        return "—"
    seconds = int(seconds)
    h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_count(n):
    if n is None:
        return "—"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def get_video_data(video_id, fields):
    data = {}

    if any(f in fields for f in ["title", "uploader", "duration", "views", "description", "comments", "like_count"]):
        ydl_opts = {"quiet": True, "getcomments": "comments" in fields}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
                data["title"] = info.get("title")
                data["uploader"] = info.get("uploader")
                data["duration"] = info.get("duration")
                data["views"] = info.get("view_count")
                data["like_count"] = info.get("like_count")
                data["description"] = info.get("description")
                if "comments" in fields:
                    data["comments"] = info.get("comments")
            except Exception as e:
                data["error_metadata"] = str(e)

    if "transcript" in fields:
        try:
            api = YouTubeTranscriptApi()
            transcript_list = api.fetch(video_id)
            data["transcript"] = " ".join([t.text for t in transcript_list])
        except Exception as e:
            data["error_transcript"] = str(e)

    return data


def format_markdown(video_id, data, fields):
    lines = []

    title = data.get("title", "Unknown")
    url = f"https://www.youtube.com/watch?v={video_id}"
    lines.append(f"## [{title}]({url})\n")

    meta = []
    if data.get("uploader"):
        meta.append(f"**Channel:** {data['uploader']}")
    if "duration" in fields or "duration" in data:
        meta.append(f"**Duration:** {fmt_duration(data.get('duration'))}")
    if "views" in fields or "views" in data:
        meta.append(f"**Views:** {fmt_count(data.get('views'))}")
    if "like_count" in fields or "like_count" in data:
        meta.append(f"**Likes:** {fmt_count(data.get('like_count'))}")
    if meta:
        lines.append(" | ".join(meta))
        lines.append("")

    if "description" in fields:
        desc = data.get("description") or "—"
        lines.append("### Description\n")
        lines.append(desc)
        lines.append("")

    if "transcript" in fields:
        if "error_transcript" in data:
            lines.append(f"### Transcript\n\n*Error: {data['error_transcript']}*")
        else:
            lines.append("### Transcript\n")
            lines.append(data.get("transcript", ""))
        lines.append("")

    if "error_metadata" in data:
        lines.append(f"> **Error fetching metadata:** {data['error_metadata']}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="YouTube video info")
    parser.add_argument("video_id", help="YouTube Video ID")
    parser.add_argument("--title", action="store_true", help="Extract title")
    parser.add_argument("--uploader", action="store_true", help="Extract uploader/channel")
    parser.add_argument("--duration", action="store_true", help="Extract duration")
    parser.add_argument("--views", action="store_true", help="Extract view count")
    parser.add_argument("--likes", action="store_true", help="Extract like count")
    parser.add_argument("--transcript", action="store_true", help="Extract transcript")
    parser.add_argument("--description", action="store_true", help="Extract description")
    parser.add_argument("--comments", action="store_true", help="Extract comments")
    parser.add_argument("--json", action="store_true", help="Output raw JSON instead of markdown")
    parser.add_argument("-o", "--output", help="Output file path")

    args = parser.parse_args()

    fields = []
    if args.title:
        fields.append("title")
    if args.uploader:
        fields.append("uploader")
    if args.duration:
        fields.append("duration")
    if args.views:
        fields.append("views")
    if args.likes:
        fields.append("like_count")
    if args.transcript:
        fields.append("transcript")
    if args.description:
        fields.append("description")
    if args.comments:
        fields.append("comments")

    if not fields:
        fields = ["title", "uploader", "duration", "views"]

    data = get_video_data(args.video_id, fields)

    if args.json:
        output = json.dumps(data, indent=2)
    elif args.output and args.transcript and "transcript" in data:
        output = data["transcript"]
    elif args.output and args.description and "description" in data:
        output = data["description"]
    else:
        output = format_markdown(args.video_id, data, fields)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
    else:
        print(output)


if __name__ == "__main__":
    main()
