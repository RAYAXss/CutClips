# core/restore.py
import os
import json
from pathlib import Path
from moviepy.editor import VideoFileClip

def restaurer_clips_json(log, json_file="data/clips_info.json", clips_folder="clips"):
    """
    Parcourt clips_folder, ajoute les mp4 non référencés dans json_file.
    """
    try:
        jf = Path(json_file)
        cf = Path(clips_folder)
        cf.mkdir(parents=True, exist_ok=True)
        if jf.exists():
            try:
                with open(jf, "r", encoding="utf-8") as f:
                    current = json.load(f)
            except Exception:
                current = []
        else:
            current = []

        existing_paths = set([os.path.abspath(c.get("clip_path")) for c in current if "clip_path" in c])

        added = 0
        for p in cf.glob("*.mp4"):
            abs_p = os.path.abspath(p)
            if abs_p in existing_paths:
                continue
            try:
                clip = VideoFileClip(str(p))
                duration = clip.duration
                clip.close()
                name = p.stem.lower()
                base = name.rsplit("_", 1)[0]
                category = "funny" if base == "clip" else base
                current.append({
                    "clip_path": str(p),
                    "start": 0.0,
                    "end": duration,
                    "duration": duration,
                    "category": category
                })
                added += 1
            except Exception as e:
                log(f"Erreur fichier {p}: {e}")

        with open(jf, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=4, ensure_ascii=False)

        log(f"✅ {added} clips restaurés dans le JSON.")
        return added
    except Exception as e:
        log(f"Erreur restaurer_clips_json: {e}")
        return 0
