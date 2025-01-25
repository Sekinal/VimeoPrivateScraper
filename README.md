# Vimeo Downloader Pro

A high-performance GUI application for downloading Vimeo videos with concurrent segment downloading and automatic audio/video merging.

![Application Screenshot](screenshot.png) *Example UI (screenshot placeholder)*

## Features

- 🚀 **High-Quality Downloads** - Automatically selects highest available bitrate
- ⚡ **Concurrent Downloads** - Downloads multiple segments simultaneously (5x faster)
- 🔁 **Resume Capability** - Automatic retries for failed segments (3 attempts)
- 🔊 **Smart Merging** - Combines video/audio tracks with FFmpeg
- 📊 **Real-Time Progress** - Separate progress bars for video and audio
- 📝 **Activity Logging** - Detailed download history and error reporting

## Prerequisites

- Python 3.8+
- FFmpeg (must be accessible system-wide)
- Required Python packages:
  ```bash
  pip install PySide6 aiohttp
  ```
- Alternatively you can use `uv` to directly run the project! (RECOMMENDED)

## Installation

1. **Install FFmpeg**:
   - Windows: [Windows Builds](https://www.gyan.dev/ffmpeg/builds/)
   - macOS: `brew install ffmpeg`
   - Linux: `sudo apt install ffmpeg`

2. **Clone repository**:
   ```bash
   git clone https://github.com/yourusername/vimeo-downloader-pro.git
   cd vimeo-downloader-pro
   ```

3. **Install Python dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

## Usage

1. **Obtain Playlist URL**:
   - Play the Vimeo video in your browser
   - Open Developer Tools (F12) → Network tab
   - Filter for `playlist` requests
   - Copy the full request URL

2. **Run the Application**:
   ```bash
   python scraper.py
   ```
   or when using `uv`
   ```bash
   uv run scraper.py
   ```

3. **GUI Workflow**:
   - Paste playlist URL in the input field
   - Choose output directory (default: `~/Downloads/VimeoDownloads`)
   - Click "Start Download"
   - Monitor progress in real-time through the interface

![Usage Demo](demo.gif) *Example Workflow (gif placeholder)*

## Troubleshooting

**Common Issues**:
- "FFmpeg not found" → Ensure FFmpeg is installed and in system PATH
- Failed downloads → Verify network connection and URL validity
- Merge errors → Check if both video/audio files were downloaded successfully

**Log Analysis**:
- Detailed error messages appear in the activity log
- Look for HTTP errors or file permission issues
- Failed segments will show retry attempts

## Legal Notice

⚠️ **Important**: Only download content you have explicit rights to access. This tool is intended for legitimate personal use and educational purposes. Respect copyright laws and Vimeo's terms of service.

## License

MIT License - See [LICENSE](LICENSE) for details

---

**Contributions Welcome!**  
Found a bug? Have a feature request? Please open an issue or submit a PR.