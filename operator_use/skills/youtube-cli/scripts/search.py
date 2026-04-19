import argparse
import json
from youtubesearchpython import VideosSearch


def format_markdown(query, results):
    videos = results.get("result", [])
    if not videos:
        return f"No results found for: {query}"

    lines = [f"## YouTube Search: {query}\n"]
    for i, v in enumerate(videos, 1):
        title = v.get("title", "Unknown")
        link = v.get("link", "")
        channel = v.get("channel", {}).get("name", "Unknown")
        duration = v.get("duration") or "—"
        published = v.get("publishedTime") or "—"
        views = v.get("viewCount", {}).get("short") or "—"

        lines.append(f"{i}. **[{title}]({link})**")
        lines.append(f"   - Channel: {channel}")
        lines.append(f"   - Duration: {duration} | Views: {views} | Published: {published}")
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="YouTube search")
    parser.add_argument("query", nargs="+", help="Search query (multiple words allowed without quotes)")
    parser.add_argument("--limit", type=int, default=5, help="Number of results to return")
    parser.add_argument("--language", default="en", help="Language code (default: en)")
    parser.add_argument("--region", default="US", help="Region code (default: US)")
    parser.add_argument("--json", action="store_true", help="Output raw JSON instead of markdown")
    parser.add_argument("-o", "--output", help="Output file path")

    args = parser.parse_args()

    query = " ".join(args.query)
    results = VideosSearch(query, limit=args.limit, language=args.language, region=args.region).result()

    if args.json:
        output = json.dumps(results, indent=2)
    else:
        output = format_markdown(query, results)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
    else:
        print(output)


if __name__ == "__main__":
    main()
