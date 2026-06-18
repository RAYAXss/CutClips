# core/scene.py
from scenedetect import VideoManager, SceneManager
from scenedetect.detectors import ContentDetector
from pathlib import Path
from moviepy.editor import VideoFileClip
from .creator import couper_et_monter

def detecter_scenes(video_path: str, category: str, seuil: float = 50.0, min_duree: float = 1.5):
    """
    Détecte les scenes via scenedetect et retourne une liste (start,end) en secondes.
    """
    try:
        vm = VideoManager([video_path])
        sm = SceneManager()
        sm.add_detector(ContentDetector(threshold=seuil))

        vm.set_downscale_factor()
        vm.start()
        sm.detect_scenes(frame_source=vm)
        scene_list = sm.get_scene_list()
        vm.release()

        filtered = [
            (start.get_seconds(), end.get_seconds())
            for start, end in scene_list
            if end.get_seconds() - start.get_seconds() >= min_duree
        ]

        # fallback si aucune scène
        if not filtered:
            clip = VideoFileClip(video_path)
            filtered = [(0.0, clip.duration)]
            clip.close()

        return filtered
    except Exception as e:
        raise RuntimeError(f"Erreur detecter_scenes: {e}")

def transcribe_video(log, category: str, downloads_folder: str = None):
    """
    Cherche le dernier mp4 dans downloads_folder (ou dans downloads/video_*) et lance detecter_scenes + couper_et_monter.
    """
    try:
        log("Analyse des scènes en cours...")
        if downloads_folder:
            d = Path(downloads_folder)
            mp4s = list(d.glob("*.mp4"))
            if not mp4s:
                log("Aucun mp4 trouvé dans le dossier de téléchargement.", "error")
                return
            video_path = str(mp4s[0])
        else:
            # fallback: cherche dans downloads/video_*/
            roots = sorted(Path("downloads").glob("video_*"), key=lambda p: int(p.name.split("_")[-1]) if "_" in p.name else 0)
            if not roots:
                log("Aucun dossier downloads trouvé.", "error")
                return
            latest = roots[-1]
            mp4s = list(latest.glob("*.mp4"))
            if not mp4s:
                log("Aucun mp4 dans le dossier downloads le plus récent.", "error")
                return
            video_path = str(mp4s[0])

        scenes = detecter_scenes(video_path, category)
        log(f"Detected clips: {len(scenes)}")
        couper_et_monter(video_path, scenes, category)
        log("Découpage et montage des clips terminés.")
    except Exception as e:
        log(f"Erreur transcribe_video: {e}", "error")


