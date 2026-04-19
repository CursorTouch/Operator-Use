import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi
import argparse
import json

def get_video_data(video_id, fields):
    data = {}

    # Get metadata if requested
    if any(f in fields for f in ['title', 'uploader', 'duration', 'views', 'description']):
        ydl_opts = {'quiet': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
                # Ensure all metadata keys exist if requested
                data['title'] = info.get('title')
                data['uploader'] = info.get('uploader')
                data['duration'] = info.get('duration')
                data['views'] = info.get('view_count')
                data['description'] = info.get('description')
            except Exception as e:
                data['error_metadata'] = str(e)

    # Get transcript if requested
    if 'transcript' in fields:
        try:
            # Using instance to fetch
            api = YouTubeTranscriptApi()
            transcript_list = api.fetch(video_id)
            data['transcript'] = " ".join([t.text for t in transcript_list])
        except Exception as e:
            data['error_transcript'] = str(e)

    return data

def main():
    parser = argparse.ArgumentParser(description="YouTube CLI processor")
    parser.add_argument("video_id", help="YouTube Video ID")
    parser.add_argument("--title", action='store_true', help="Extract title")
    parser.add_argument("--uploader", action='store_true', help="Extract uploader")
    parser.add_argument("--duration", action='store_true', help="Extract duration")
    parser.add_argument("--views", action='store_true', help="Extract views")
    parser.add_argument("--transcript", action='store_true', help="Extract transcript")
    parser.add_argument("--description", action='store_true', help="Extract description")
    parser.add_argument("-o", "--output", help="Output file path to save JSON result")

    args = parser.parse_args()

    fields = []
    if args.title:
        fields.append('title')
    if args.uploader:
        fields.append('uploader')
    if args.duration:
        fields.append('duration')
    if args.views:
        fields.append('views')
    if args.transcript:
        fields.append('transcript')

    if not fields:
        fields = ['title', 'uploader', 'duration', 'views']

    result = get_video_data(args.video_id, fields)
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            if args.transcript and 'transcript' in result:
                f.write(result['transcript'])
            elif args.description and 'description' in result:
                f.write(result['description'])
            else:
                json.dump(result, f, indent=2)
    else:
        print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
