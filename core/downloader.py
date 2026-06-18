# core/downloader.py
import os
import ssl
import certifi
from pathlib import Path
from yt_dlp import YoutubeDL
from .scene import transcribe_video


def download_video(url: str, log, category: str = "funny", base_output_path: str = "downloads"):
    """
    Télécharge une vidéo via yt_dlp dans downloads/video_N/ et appelle transcribe_video.
    log: fonction de logging (ex: main.log)
    """
    try:
        log(f"Début téléchargement : {url}")
        base_output_path = Path(base_output_path)
        base_output_path.mkdir(parents=True, exist_ok=True)

        video_folders = sorted(
            base_output_path.glob("video_*"),
            key=lambda p: int(p.name.split("_")[-1]) if "_" in p.name else 0
        )
        new_folder_index = (int(video_folders[-1].name.split("_")[-1]) + 1) if video_folders else 1
        video_folder = base_output_path / f"video_{new_folder_index}"
        video_folder.mkdir(parents=True, exist_ok=True)

        def progress_hook(d):
            if d.get("status") == "downloading":
                pct = d.get("_percent_str", "").strip()
                log(f"Téléchargement: {pct}")
            elif d.get("status") == "finished":
                log("Téléchargement fini, post-traitement...")

        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/mp4',
            'outtmpl': str(video_folder / '%(title)s.%(ext)s'),
            'nocheckcertificate': True,   # yt-dlp level
            'cookiefile': 'cookies.txt',
            'progress_hooks': [progress_hook],
            'quiet': True,
            'no_warnings': True,
            'compat_opts': ['no-youtube-unavailable-videos'],
            # Pass certifi bundle so yt-dlp's internal requests use it
            'ssl_certificate': certifi.where(),
        }

        # Also patch the global SSL context for urllib (used by TikTok requests)
        _orig_context = ssl._create_default_https_context
        ssl._create_default_https_context = ssl._create_unverified_context

        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
        finally:
            # Always restore the original SSL context after download
            ssl._create_default_https_context = _orig_context

        log("Extraction terminée, lancement de l'analyse de scènes...")
        transcribe_video(log, category, downloads_folder=str(video_folder))

    except Exception as e:
        log(f"Erreur download_video: {e}")