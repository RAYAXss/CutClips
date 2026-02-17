# core/categories.py
import json
import random
from pathlib import Path


CLIPS_FOLDER = Path("clips")

def get_categories_from_clips():
    categories = set()
    for p in CLIPS_FOLDER.glob("*.mp4"):
        name = p.stem
        if "_" in name:
            cat = name.split("_")[0].lower()
        else:
            cat = "other"
        categories.add(cat)
    return sorted(list(categories))

STATS_FILE = Path("data/stats.json")

def load_weights():
    # categories réellement présentes dans clips/
    cats = get_categories_from_clips()
    # Aucun clip → aucun poids
    if not cats:
        return {}

    # Charger poids existants
    if STATS_FILE.exists():
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                stored = json.load(f)
        except:
            stored = {}
    else:
        stored = {}

    # Vérifier si catégorie nouvelle ajoutée
    missing = [c for c in cats if c not in stored]

    if missing:
        # répartir uniformément
        n = len(cats)
        w = 1 / n
        stored = {c: w for c in cats}

        # sauvegarder immédiatement
        save_weights(stored)

    total = sum(stored.values())
    if total != 0:
        stored = {c: (v / total) for c, v in stored.items()}

    # Retourner les poids normalisés
    return stored


def save_weights(weights: dict):
    total = sum(weights.values())
    if total == 0:
        return
    normalized = {k: v / total for k, v in weights.items()}
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(normalized, f, indent=4, ensure_ascii=False)


def choose_weighted_category():
    weights = load_weights()
    if not weights:
        return None
    categories = list(weights.keys())
    vals = list(weights.values())
    try:
        chosen = random.choices(categories, weights=vals, k=1)[0]
    except Exception:
        # fallback uniform
        chosen = random.choice(categories)
    return chosen
