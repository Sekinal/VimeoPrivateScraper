import sys
import base64
import os
import subprocess
import asyncio
from urllib.parse import urlparse, parse_qs, urlencode
from typing import Optional, Tuple, List

import aiohttp
from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
                               QLineEdit, QPushButton, QProgressBar, QLabel,
                               QTextEdit, QStyle, QFileDialog, QMessageBox)
from PySide6.QtGui import QFont, QColor, QPalette


class DownloadWorker(QObject):
    progress_updated = Signal(int, int)  # track_type (0=video,1=audio), percentage
    log_message = Signal(str)
    finished = Signal(bool, str)

    CONCURRENT_DOWNLOADS = 5
    RETRY_ATTEMPTS = 3
    TIMEOUT = 30

    def __init__(self, playlist_url: str, output_dir: str):
        super().__init__()
        self.playlist_url = playlist_url
        self.output_dir = output_dir
        self._is_running = True

    def stop(self):
        self._is_running = False

    def build_segment_url(self, playlist_url: str, segment_path: str, segment_query: str) -> str:
        """Construct full segment URL using playlist URL components"""
        parsed = urlparse(playlist_url)
        path_parts = parsed.path.split('/')
        if 'playlist' in path_parts:
            range_index = path_parts.index('playlist')
            new_path = '/'.join(path_parts[:range_index] + ['range', 'prot', segment_path])
        else:
            new_path = parsed.path

        base_params = parse_qs(parsed.query)
        segment_params = parse_qs(segment_query)
        combined_params = {**base_params, **segment_params}
        
        return parsed._replace(
            path=new_path,
            query=urlencode(combined_params, doseq=True)
        ).geturl()

    async def download_segment(self, session: aiohttp.ClientSession, semaphore: asyncio.Semaphore,
                              idx: int, url: str) -> Tuple[int, Optional[bytes]]:
        """Download a single segment with retries and rate limiting"""
        async with semaphore:
            for retry in range(self.RETRY_ATTEMPTS):
                if not self._is_running:
                    return (idx, None)
                
                try:
                    async with session.get(url, timeout=self.TIMEOUT) as response:
                        if response.status == 200:
                            content = await response.read()
                            return (idx, content)
                        else:
                            await asyncio.sleep(2 ** retry)
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    if retry == self.RETRY_ATTEMPTS - 1:
                        self.log_message.emit(f"Error downloading segment {idx+1}: {str(e)}")
                        return (idx, None)
                    await asyncio.sleep(2 ** retry)
            return (idx, None)

    async def async_download_track(self, track_type: str, track_data: dict, playlist_url: str) -> Optional[str]:
        """Asynchronously download a track (video/audio)"""
        os.makedirs(self.output_dir, exist_ok=True)
        filename = os.path.join(self.output_dir, f'{track_type}.mp4')

        try:
            init_segment = base64.b64decode(track_data['init_segment'])
            with open(filename, 'wb') as f:
                f.write(init_segment)
        except Exception as e:
            self.log_message.emit(f"Error writing init segment: {str(e)}")
            return None

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Referer': 'https://vimeo.com/'
        }

        segments = []
        for idx, segment in enumerate(track_data['segments']):
            if not self._is_running:
                return None
            
            segment_path, _, segment_query = segment['url'].partition('?')
            segment_url = self.build_segment_url(playlist_url, segment_path, segment_query)
            segments.append((idx, segment_url))

        connector = aiohttp.TCPConnector(limit=self.CONCURRENT_DOWNLOADS)
        async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
            semaphore = asyncio.Semaphore(self.CONCURRENT_DOWNLOADS)
            tasks = [self.download_segment(session, semaphore, idx, url) for idx, url in segments]
            downloaded_segments = []

            for future in asyncio.as_completed(tasks):
                if not self._is_running:
                    for task in tasks:
                        task.cancel()
                    break
                
                idx, content = await future
                if content is None:
                    self.log_message.emit(f"Failed to download segment {idx+1}, aborting.")
                    return None
                
                downloaded_segments.append((idx, content))
                progress = int((len(downloaded_segments) / len(segments)) * 100)
                self.progress_updated.emit(0 if track_type == 'video' else 1, progress)

            if not self._is_running:
                return None

            downloaded_segments.sort(key=lambda x: x[0])
            try:
                with open(filename, 'ab') as f:
                    for idx, content in downloaded_segments:
                        f.write(content)
            except IOError as e:
                self.log_message.emit(f"File write error: {str(e)}")
                return None

        return filename

    def merge_files(self, video_path: str, audio_path: str) -> Optional[str]:
        """Merge video and audio tracks using ffmpeg"""
        merged_dir = os.path.join(self.output_dir, 'merged')
        os.makedirs(merged_dir, exist_ok=True)
        output_path = os.path.join(merged_dir, 'final_video.mp4')

        try:
            subprocess.run(
                [
                    'ffmpeg',
                    '-y',
                    '-i', video_path,
                    '-i', audio_path,
                    '-c', 'copy',
                    output_path
                ],
                check=True,
                capture_output=True
            )
            return output_path
        except subprocess.CalledProcessError as e:
            self.log_message.emit(f"Merge failed: {e.stderr.decode()}")
            return None
        except FileNotFoundError:
            self.log_message.emit("FFmpeg not found. Please install FFmpeg.")
            return None

    async def async_run(self):
        """Main async workflow"""
        try:
            self.log_message.emit("Fetching playlist data...")
            async with aiohttp.ClientSession() as session:
                async with session.get(self.playlist_url) as response:
                    json_data = await response.json()

            video_track = max(
                [v for v in json_data['video'] if v['mime_type'] == 'video/mp4'],
                key=lambda x: x.get('bitrate', 0)
            )
            audio_track = next(
                a for a in json_data['audio'] 
                if a['audio_primary'] and a['mime_type'] == 'audio/mp4'
            )

            self.log_message.emit("\nStarting video download...")
            video_file = await self.async_download_track('video', video_track, self.playlist_url)
            if not video_file or not self._is_running:
                self.finished.emit(False, "Video download failed")
                return

            self.log_message.emit("\nStarting audio download...")
            audio_file = await self.async_download_track('audio', audio_track, self.playlist_url)
            if not audio_file or not self._is_running:
                self.finished.emit(False, "Audio download failed")
                return

            self.log_message.emit("\nMerging tracks...")
            final_path = self.merge_files(video_file, audio_file)
            if final_path and self._is_running:
                self.log_message.emit(f"\nSuccess! Video saved to:\n{final_path}")
                self.finished.emit(True, final_path)
            else:
                self.finished.emit(False, "Merge failed")

        except Exception as e:
            self.log_message.emit(f"Critical error: {str(e)}")
            self.finished.emit(False, str(e))

    def run(self):
        """QThread entry point with async event loop"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.async_run())
        finally:
            loop.close()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Vimeo Downloader Pro")
        self.setGeometry(100, 100, 900, 700)
        self.setup_ui()
        self.download_thread = None
        self.worker = None

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # Modern color scheme
        self.colors = {
            "background": "#1A1A2E",
            "surface": "#16213E",
            "primary": "#0F3460",
            "secondary": "#E94560",
            "text": "#FFFFFF"
        }

        # Apply custom style
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {self.colors['background']};
                color: {self.colors['text']};
                font-family: 'Segoe UI', sans-serif;
            }}
            QLineEdit {{
                background-color: {self.colors['surface']};
                border: 2px solid {self.colors['primary']};
                border-radius: 8px;
                padding: 12px;
                font-size: 14px;
                selection-background-color: {self.colors['secondary']};
            }}
            QPushButton {{
                background-color: {self.colors['primary']};
                border: none;
                padding: 14px 28px;
                border-radius: 8px;
                font-size: 14px;
                font-weight: 500;
                text-transform: uppercase;
                transition: all 0.3s ease;
            }}
            QPushButton:hover {{
                background-color: {self.colors['secondary']};
                transform: translateY(-1px);
            }}
            QPushButton:pressed {{
                transform: translateY(0);
            }}
            QPushButton:disabled {{
                background-color: #555555;
                color: #AAAAAA;
            }}
            QProgressBar {{
                border: 2px solid {self.colors['primary']};
                border-radius: 8px;
                text-align: center;
                height: 24px;
                font-size: 12px;
            }}
            QProgressBar::chunk {{
                background-color: {self.colors['secondary']};
                border-radius: 6px;
                margin: 2px;
            }}
            QTextEdit {{
                background-color: {self.colors['surface']};
                border: 2px solid {self.colors['primary']};
                border-radius: 8px;
                padding: 10px;
                font-family: 'Consolas', monospace;
                font-size: 12px;
            }}
            QFileDialog {{
                background-color: {self.colors['background']};
                color: {self.colors['text']};
            }}
        """)

        # Header
        header = QLabel("Vimeo Downloader Pro")
        header.setStyleSheet(f"""
            font-size: 24px;
            font-weight: bold;
            color: {self.colors['secondary']};
            padding: 15px 0;
            qproperty-alignment: AlignCenter;
        """)
        layout.addWidget(header)

        # Input Section
        input_group = QGroupBox("Download Settings")
        input_group.setStyleSheet(f"""
            QGroupBox {{
                border: 2px solid {self.colors['primary']};
                border-radius: 10px;
                margin-top: 10px;
                padding-top: 20px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                color: {self.colors['secondary']};
            }}
        """)
        input_layout = QVBoxLayout(input_group)
        input_layout.setContentsMargins(15, 15, 15, 15)
        input_layout.setSpacing(15)

        # URL Input
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Enter Vimeo playlist URL...")
        self.url_input.setClearButtonEnabled(True)
        input_layout.addWidget(self.url_input)

        # Directory Selection
        dir_layout = QHBoxLayout()
        self.dir_label = QLabel("Output Directory:")
        self.dir_label.setStyleSheet(f"color: {self.colors['secondary']};")
        self.dir_display = QLineEdit()
        self.dir_display.setReadOnly(True)
        self.browse_btn = QPushButton("Browse")
        self.browse_btn.setIcon(self.style().standardIcon(QStyle.SP_DirOpenIcon))
        self.browse_btn.clicked.connect(self.choose_output_dir)
        dir_layout.addWidget(self.dir_label)
        dir_layout.addWidget(self.dir_display)
        dir_layout.addWidget(self.browse_btn)
        input_layout.addLayout(dir_layout)

        layout.addWidget(input_group)

        # Progress Section
        progress_group = QGroupBox("Download Progress")
        progress_group.setStyleSheet(input_group.styleSheet())
        progress_layout = QVBoxLayout(progress_group)
        progress_layout.setContentsMargins(15, 15, 15, 15)
        progress_layout.setSpacing(15)

        # Video Progress
        self.video_progress = QProgressBar()
        self.video_progress.setFormat("Video Progress: %p%")
        progress_layout.addWidget(self.video_progress)

        # Audio Progress
        self.audio_progress = QProgressBar()
        self.audio_progress.setFormat("Audio Progress: %p%")
        progress_layout.addWidget(self.audio_progress)

        layout.addWidget(progress_group)

        # Control Buttons
        btn_layout = QHBoxLayout()
        self.download_btn = QPushButton("Start Download")
        self.download_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.download_btn.clicked.connect(self.toggle_download)
        btn_layout.addWidget(self.download_btn)

        layout.addLayout(btn_layout)

        # Log Output
        log_group = QGroupBox("Activity Log")
        log_group.setStyleSheet(input_group.styleSheet())
        log_layout = QVBoxLayout(log_group)
        self.log_output = QTextEdit()
        self.log_output.setPlaceholderText("Download activity will appear here...")
        log_layout.addWidget(self.log_output)
        layout.addWidget(log_group)

        # Status Bar
        self.status_bar = QLabel()
        self.status_bar.setStyleSheet(f"color: {self.colors['secondary']}; font-weight: bold;")
        layout.addWidget(self.status_bar)

        # Set default output dir
        self.output_dir = os.path.expanduser("~/Downloads/VimeoDownloads")
        self.dir_display.setText(self.output_dir)

    def choose_output_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select Output Directory")
        if dir_path:
            self.output_dir = dir_path
            self.status_bar.setText(f"Output directory: {dir_path}")

    def toggle_download(self):
        if self.download_btn.text() == "Start Download":
            self.start_download()
        else:
            self.cancel_download()

    def start_download(self):
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "Error", "Please enter a valid URL")
            return

        if not os.path.exists(self.output_dir):
            try:
                os.makedirs(self.output_dir)
            except OSError:
                QMessageBox.warning(self, "Error", "Invalid output directory")
                return

        self.log_output.clear()
        self.video_progress.setValue(0)
        self.audio_progress.setValue(0)
        self.download_btn.setText("Cancel Download")
        self.download_btn.setStyleSheet("background-color: #f44336;")

        self.download_thread = QThread()
        self.worker = DownloadWorker(url, self.output_dir)
        self.worker.moveToThread(self.download_thread)

        # Connect signals
        self.worker.progress_updated.connect(self.update_progress)
        self.worker.log_message.connect(self.append_log)
        self.worker.finished.connect(self.download_finished)
        self.download_thread.started.connect(self.worker.run)
        self.download_thread.finished.connect(self.download_thread.deleteLater)

        self.download_thread.start()

    def cancel_download(self):
        if self.worker:
            self.worker.stop()
        self.download_btn.setEnabled(False)
        self.append_log("\nDownload cancelled by user")

    def update_progress(self, track_type, value):
        if track_type == 0:
            self.video_progress.setValue(value)
        else:
            self.audio_progress.setValue(value)

    def append_log(self, message):
        self.log_output.append(message)
        self.log_output.ensureCursorVisible()

    def download_finished(self, success, message):
        self.download_thread.quit()
        self.download_thread.wait()
        
        self.download_btn.setText("Start Download")
        self.download_btn.setStyleSheet("")
        self.download_btn.setEnabled(True)

        if success:
            self.status_bar.setText(f"Download completed: {message}")
            QMessageBox.information(self, "Success", "Download completed successfully!")
        else:
            self.status_bar.setText(f"Error: {message}")
            QMessageBox.critical(self, "Error", message)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())