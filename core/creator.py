# core/creator.py
import os
import json
import random
from pathlib import Path
from moviepy.editor import VideoFileClip, concatenate_videoclips
from .categories import choose_weighted_category

CLIPS_FOLDER = Path("clips")
DATA_CLIPS_JSON = Path("data/clips_info.json")
ASSETS_DIR = Path("assets")
SEPARATOR_NAME = ASSETS_DIR / "catcore_video.mp4"

CLIPS_FOLDER.mkdir(parents=True, exist_ok=True)
DATA_CLIPS_JSON.parent.mkdir(parents=True, exist_ok=True)
if not DATA_CLIPS_JSON.exists():
    DATA_CLIPS_JSON.write_text("[]", encoding="utf-8")

def couper_et_monter(video_path: str, scenes: list, category: str,
                     output_dir: str = "clips",
                     json_file: str = str(DATA_CLIPS_JSON)):

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    clips = []

    # ouverture unique de la vidéo (pas de gestion avancée)
    base_clip = VideoFileClip(video_path)

    try:
        with open(json_file, "r", encoding="utf-8") as f:
            existing = json.load(f)
    except Exception:
        existing = []

    existing_files = len(list(output_dir.glob("*.mp4")))
    idx = existing_files + 1

    for raw_start, raw_end in scenes:
        start = raw_start
        end = raw_end - 0.001

        if end - start < 1:
            continue

        try:
            subclip = base_clip.subclip(start, end)

            clip_name = f"{category}_{idx:03d}.mp4"
            clip_path = output_dir / clip_name
            idx += 1

            # ⚠️ volontairement simple et stable
            subclip.write_videofile(
                str(clip_path),
                codec="libx264",
                audio_codec="aac",
                logger=None
            )

            clips.append({
                "clip_path": str(clip_path),
                "start": start,
                "end": end,
                "duration": end - start,
                "category": category
            })

        except Exception as e:
            print(f"Erreur couper_et_monter scène {start}-{end}: {e}")

    # fermeture globale uniquement à la fin
    base_clip.close()

    existing.extend(clips)
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=4, ensure_ascii=False)

def _load_clips(json_file: str = str(DATA_CLIPS_JSON)):
    try:
        with open(json_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def create_video_from_clips(log, filename: str, tiktok: bool, category: str, cible: float, marge: float, use_catcore: bool, output_folder: str):
    """
    Compose une vidéo finale à partir des clips disponibles.
    - Choix de catégorie: si category vide ou placeholder -> choix pondéré.
    - output_folder: dossier de sortie.
    """
    try:
        clips = _load_clips()
        if not clips:
            log("Aucun clip disponible dans le JSON.", "error")
            return None

        # choose category
        if not category or category == "Choose or create category":
            chosen = choose_weighted_category()
            log(f"Catégorie choisie (pondérée) : {chosen}")
        else:
            chosen = category

            
        
        # filter clips by category start name
        clips_filtered = [c for c in clips if Path(c["clip_path"]).stem.startswith(chosen)]
        if not clips_filtered:
            log(f"Aucun clip trouvé pour la catégorie {chosen}.", "error")
            return None

        random.shuffle(clips_filtered)
        selected = []
        used = set()
        total = 0.0
        target_min = max(cible - marge, 0)
        target_max = cible + marge

        for c in clips_filtered:
            p = c["clip_path"]
            if p in used:
                continue
            try:
                clip = VideoFileClip(p)
            except Exception:
                continue
            if clip.duration < 0.5:
                clip.close()
                continue
            if total + clip.duration > target_max:
                clip.close()
                continue
            selected.append(clip)
            used.add(p)
            total += clip.duration
            if total >= target_min:
                break

        if not selected:
            log("Aucun clip sélectionné après filtrage.", "error")
            return None

        # resize/crop to vertical if needed
        target_w, target_h = (1080, 1920) if tiktok else (1920, 1080)
        processed = []
        for clip in selected:
            # ensure proper orientation/size
            try:
                clip_resized = clip.resize(height=target_h)
                if clip_resized.w < target_w:
                    # pad with black
                    clip_resized = clip_resized.on_color(size=(target_w, target_h), color=(0,0,0), pos=("center","center"))
                else:
                    clip_resized = clip_resized.crop(x_center=clip_resized.w/2, width=target_w)
                processed.append(clip_resized)
            except Exception as e:
                clip.close()

        # separator option
        separator_clip = None
        if use_catcore and SEPARATOR_NAME.exists():
            try:
                separator_clip = VideoFileClip(str(SEPARATOR_NAME))
            except Exception:
                separator_clip = None

        final_sequence = []
        for i, c in enumerate(processed):
            final_sequence.append(c)
            if i < len(processed) - 1 and separator_clip:
                final_sequence.append(separator_clip)

        final = concatenate_videoclips(final_sequence, method="compose")
        out_folder = Path(output_folder)
        out_folder.mkdir(parents=True, exist_ok=True)
        index = len(list(out_folder.glob("*.mp4"))) + 1
        out_path = out_folder / f"{chosen}_{filename}_{index}.mp4"
        final.write_videofile(str(out_path), codec="libx264", audio_codec="aac")
        final.close()
        # close clips
        for c in processed:
            try:
                c.close()
            except:
                pass
        log(f"Vidéo finale enregistrée: {out_path}", "success")
        return str(out_path)
    except Exception as e:
        log(f"Erreur create_video_from_clips: {e}", "error")
        return None

def create_video_batch(nb: int, filename: str, tiktok: bool, category: str, log, cible: float, marge: float, use_catcore: bool, output_folder: str):
    for i in range(nb):
        create_video_from_clips(log, filename, tiktok, category, cible, marge, use_catcore, output_folder)
