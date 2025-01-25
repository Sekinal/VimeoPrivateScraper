import sys
import base64
import os
import subprocess
from urllib.parse import urlparse, parse_qs, urlencode

import requests
from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                               QLineEdit, QPushButton, QProgressBar, QLabel,
                               QTextEdit, QStyle, QFileDialog, QMessageBox)
from PySide6.QtGui import QFont, QColor, QPalette


class DownloadWorker(QObject):
    progress_updated = Signal(int, int)  # track_type (0=video,1=audio), percentage
    log_message = Signal(str)
    finished = Signal(bool, str)

    def __init__(self, playlist_url, output_dir):
        super().__init__()
        self.playlist_url = playlist_url
        self.output_dir = output_dir
        self._is_running = True

    def stop(self):
        self._is_running = False

    def build_segment_url(self, playlist_url, segment_path, segment_query):
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

    def download_track(self, track_type, track_data, playlist_url):
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

        total_segments = len(track_data['segments'])
        for idx, segment in enumerate(track_data['segments']):
            if not self._is_running:
                return None

            segment_path, _, segment_query = segment['url'].partition('?')
            segment_url = self.build_segment_url(playlist_url, segment_path, segment_query)
            
            self.log_message.emit(f'Downloading {track_type} segment {idx+1}/{total_segments}')
            try:
                response = requests.get(segment_url, headers=headers, timeout=10)
                if response.status_code == 200:
                    with open(filename, 'ab') as f:
                        f.write(response.content)
                    progress = int((idx + 1) / total_segments * 100)
                    self.progress_updated.emit(0 if track_type == 'video' else 1, progress)
                else:
                    self.log_message.emit(f'Failed to download segment {idx+1} (HTTP {response.status_code})')
                    return None
            except Exception as e:
                self.log_message.emit(f'Download error: {str(e)}')
                return None

        return filename

    def merge_files(self, video_path, audio_path):
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

    def run(self):
        try:
            self.log_message.emit("Fetching playlist data...")
            response = requests.get(self.playlist_url, timeout=10)
            json_data = response.json()

            video_track = max(
                [v for v in json_data['video'] if v['mime_type'] == 'video/mp4'],
                key=lambda x: x.get('bitrate', 0)
            )
            audio_track = next(
                a for a in json_data['audio'] 
                if a['audio_primary'] and a['mime_type'] == 'audio/mp4'
            )

            self.log_message.emit("\nStarting video download...")
            video_file = self.download_track('video', video_track, self.playlist_url)
            if not video_file or not self._is_running:
                self.finished.emit(False, "Video download failed")
                return

            self.log_message.emit("\nStarting audio download...")
            audio_file = self.download_track('audio', audio_track, self.playlist_url)
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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Vimeo Downloader")
        self.setGeometry(100, 100, 800, 600)
        self.setup_ui()
        self.download_thread = None
        self.worker = None

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # Dark theme
        self.setStyleSheet("""
            QWidget {
                background-color: #2D2D2D;
                color: #FFFFFF;
            }
            QLineEdit {
                background-color: #404040;
                border: 1px solid #606060;
                padding: 8px;
                border-radius: 4px;
            }
            QPushButton {
                background-color: #4CAF50;
                border: none;
                padding: 10px 20px;
                border-radius: 4px;
                color: white;
            }
            QPushButton:hover {
                background-color: #45A049;
            }
            QPushButton:disabled {
                background-color: #666666;
            }
            QProgressBar {
                border: 1px solid #444444;
                border-radius: 4px;
                text-align: center;
            }
            QProgressBar::chunk {
                background-color: #4CAF50;
                width: 20px;
            }
        """)

        # URL Input
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Enter playlist URL...")
        layout.addWidget(self.url_input)

        # Browse Button
        self.browse_btn = QPushButton("Choose Output Directory")
        self.browse_btn.clicked.connect(self.choose_output_dir)
        layout.addWidget(self.browse_btn)

        # Download Button
        self.download_btn = QPushButton("Start Download")
        self.download_btn.clicked.connect(self.toggle_download)
        layout.addWidget(self.download_btn)

        # Progress Bars
        self.video_progress = QProgressBar()
        self.video_progress.setFormat("Video: %p%")
        layout.addWidget(self.video_progress)

        self.audio_progress = QProgressBar()
        self.audio_progress.setFormat("Audio: %p%")
        layout.addWidget(self.audio_progress)

        # Log Output
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setFont(QFont("Consolas", 10))
        layout.addWidget(self.log_output)

        # Status Bar
        self.status_bar = QLabel()
        layout.addWidget(self.status_bar)

        # Set default output dir
        self.output_dir = os.path.expanduser("~/Downloads/VimeoDownloads")

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