import requests
import base64
import os
import subprocess
from urllib.parse import urlparse, urlunparse, urljoin, parse_qs, urlencode

def build_segment_url(playlist_url, segment_path, segment_query):
    """Construct full segment URL using playlist URL components"""
    parsed = urlparse(playlist_url)
    
    # Replace '/playlist/av/...' with '/range/prot/' in path
    path_parts = parsed.path.split('/')
    range_index = path_parts.index('playlist')
    new_path = '/'.join(path_parts[:range_index] + ['range', 'prot', segment_path])
    
    # Combine query parameters
    base_params = parse_qs(parsed.query)
    segment_params = parse_qs(segment_query)
    combined_params = {**base_params, **segment_params}
    
    # Rebuild URL
    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        new_path,
        '',
        urlencode(combined_params, doseq=True),
        ''
    ))

def download_track(track_type, track_data, playlist_url, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    filename = os.path.join(output_dir, f'{track_type}.mp4')
    
    # Write init segment
    init_segment = base64.b64decode(track_data['init_segment'])
    with open(filename, 'wb') as f:
        f.write(init_segment)
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Referer': 'https://vimeo.com/'
    }
    
    for idx, segment in enumerate(track_data['segments']):
        # Parse segment URL components
        segment_path, _, segment_query = segment['url'].partition('?')
        
        # Build full URL
        segment_url = build_segment_url(playlist_url, segment_path, segment_query)
        print(f'Downloading {track_type} segment {idx+1}: {segment_url}')
        
        response = requests.get(segment_url, headers=headers)
        if response.status_code == 200:
            with open(filename, 'ab') as f:
                f.write(response.content)
        else:
            print(f'Failed to download segment {idx+1} (HTTP {response.status_code})')
    
    print(f'Completed {track_type} download: {filename}')
    return filename

def merge_audio_video(video_path, audio_path, output_dir):
    """Merge audio and video tracks using FFmpeg"""
    merged_dir = os.path.join(output_dir, 'merged')
    os.makedirs(merged_dir, exist_ok=True)
    output_path = os.path.join(merged_dir, 'final_video.mp4')
    
    try:
        subprocess.run(
            [
                'ffmpeg',
                '-y',  # Overwrite output file without asking
                '-i', video_path,
                '-i', audio_path,
                '-c', 'copy',  # Copy streams without re-encoding
                output_path
            ],
            check=True,
            capture_output=True
        )
        print(f"\nSuccessfully merged video and audio at: {output_path}")
        return output_path
    except subprocess.CalledProcessError as e:
        print(f"\nFFmpeg merge failed: {e.stderr.decode()}")
        return None
    except FileNotFoundError:
        print("\nFFmpeg not found. Please install FFmpeg and ensure it's in your system PATH.")
        return None

# Usage
playlist_url = 'https://vod-adaptive-ak.vimeocdn.com/exp=1737833309~acl=%2F7c3b2482-aff4-4d62-824d-da525d6ac20d%2F%2A~hmac=e51f63d0b21f7002b23b201dbb29ddfdd1cafe45f25a0c886b96e26e0cdc2a71/7c3b2482-aff4-4d62-824d-da525d6ac20d/v2/playlist/av/primary/prot/cXNyPTE/playlist.json?omit=av1-hevc&pathsig=8c953e4f~ZdPLAxFka3izrlyiwcxyJCZz8NVVadcne4kQ3-qy8Pw&qsr=1&rh=2ZdMCj'
output_dir = 'downloads'

# Fetch playlist data
print("Fetching playlist data...")
response = requests.get(playlist_url)
json_data = response.json()

# Select tracks (example: highest video + primary audio)
video_track = max(
    [v for v in json_data['video'] if v['mime_type'] == 'video/mp4'],
    key=lambda x: x.get('bitrate', 0)
)

audio_track = next(
    a for a in json_data['audio'] 
    if a['audio_primary'] and a['mime_type'] == 'audio/mp4'
)

# Download tracks
print("\nStarting downloads...")
video_file = download_track('video', video_track, playlist_url, output_dir)
audio_file = download_track('audio', audio_track, playlist_url, output_dir)

# Merge tracks
print("\nMerging audio and video...")
final_video = merge_audio_video(video_file, audio_file, output_dir)

if final_video:
    print("\nProcess completed successfully!")
    print(f"Final video location: {final_video}")
else:
    print("\nProcess completed with errors")