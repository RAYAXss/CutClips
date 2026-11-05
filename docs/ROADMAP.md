# ClipTwist — Roadmap (état au 2026-11-04)

## Avancement au 2026-11-05

Phases 0 à 4 implémentées (voir le détail plus bas). Tests : `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests` (40 tests).

- Phase 0 : `cookies.txt` retiré de l'index git (pas de l'historique), clé Flask lue dans `.env` ou générée dans `data/secret_key`, mot de passe admin aléatoire au premier lancement, toutes les routes derrière la connexion (`before_request`), logs rendus via `textContent`, actions destructrices en POST, cookie `SameSite=Lax`, Socket.IO limité aux origines locales.
- Phase 1 : bug Catcore corrigé, noms de sortie horodatés + suffixe aléatoire, séparateur redimensionné, clips fermés dans un `finally`, file de jobs unique (`core/jobs.py`) avec progression, dossiers persistés dans `data/settings.json`, dossier d'entrée utilisé par la restauration, entrées de formulaire bornées.
- Phase 2 : `templates/` + `static/`, icônes SVG Lucide, Bibliothèque, panneau de jobs, toasts, page Dossiers (chemin saisi ou bouton Parcourir), page Comptes. Interface en français.
- Phase 3 : `core/editor.py` (ffmpeg direct). Volume vidéo, musique (import, volume, début, fondu, boucle) et textes horodatés (`drawtext` via `textfile`). `amix` de ffmpeg 4.2 divise par 2 : compensé par `volume=2` puis `alimiter`, vérifié à la mesure. Avec du texte la vidéo est réencodée, sinon `-c:v copy`.
- Phase 4 : `core/tiktok.py`. App TikTok de type **Desktop** (seul type qui accepte `http://127.0.0.1:5000/tiktok/callback/`), PKCE avec challenge en SHA256 **hex**, refresh automatique (token 2 h, refresh 180 j avec rotation), `creator_info` à chaque affichage de la page de publication, upload par morceaux de 10 Mo, mode brouillon (défaut) ou publication directe.

Reste à faire : brancher `core/scheduler.py`, OAuth YouTube/Instagram/Reddit, test réel de TikTok avec l'app développeur de l'utilisateur, purge de `cookies.txt` de l'historique git.

Document de passation pour un agent ou un dev qui reprend le projet.
Il décrit l'existant, les bugs constatés et ce qu'il reste à faire, dans l'ordre.

## Objectif

Application locale (Flask + Socket.IO, ouverte dans le navigateur) qui :
1. télécharge des clips (yt-dlp) et les classe par catégorie ;
2. génère des vidéos verticales 1080x1920 en assemblant des clips ;
3. **(à faire)** permet de retoucher une vidéo générée : musique de fond, volume de la vidéo baissé, musique plus forte ;
4. **(à faire)** se connecte au compte TikTok de l'utilisateur et publie directement depuis l'UI.

Proposés par l'agent, non validés par l'utilisateur :
Non-objectifs : éditeur multipiste complet type CapCut pro (keyframes, effets, transitions avancées), multi-utilisateur en ligne, hébergement public.
Anti-objectif : une app qui publie sur TikTok sans que l'utilisateur ait vu/validé la vidéo.

## Existant

| Fichier | Rôle |
|---|---|
| `main.py` | App Flask, routes, auth (users.json), HTML inline (`LOGIN_HTML`, `DASHBOARD_HTML`), logs poussés via Socket.IO |
| `core/downloader.py` | `download_video(url, log, cat)` via yt-dlp |
| `core/scene.py` | détection de scènes (PySceneDetect) |
| `core/creator.py` | `create_video_from_clips` / `create_video_batch` (moviepy 1.0.3) |
| `core/categories.py` | poids des catégories |
| `core/restore.py` | reconstruit `data/clips_info.json` depuis `clips/` |
| `core/uploader.py` | `upload_tiktok`, `upload_youtube`, `upload_instagram`, `upload_reddit`, `dispatch_upload` — **non branchés à l'UI** |
| `core/scheduler.py` | file de publication + worker (`init_scheduler`, `auto_schedule`) — **non branché à l'UI** |

Le docstring de `upload_tiktok` mentionne `/schedule/setup` : cette route n'existe pas.

## Phase 0 — Sécurité (à faire en premier)

- [ ] `cookies.txt` (cookies de session yt-dlp) est **versionné dans git et déjà poussé** sur `origin` (github.com/RAYAXss/CutClips). Le retirer : `git rm --cached cookies.txt`, l'ajouter à `.gitignore`. Considérer les sessions compromises : se déconnecter de tous les appareils sur les comptes concernés. Si le repo est public, purger l'historique (`git filter-repo`) — le retrait seul ne suffit pas.
- [ ] `app.secret_key = "cliptwist_secret"` codé en dur : lire depuis `.env` (`FLASK_SECRET_KEY`), sinon générer et persister une clé aléatoire dans `data/`.
- [ ] Compte par défaut `admin/admin` : forcer le changement de mot de passe à la première connexion (ou générer un mot de passe aléatoire affiché une fois dans la console).
- [ ] Routes sans contrôle `is_logged()` : `/download`, `/create`, `/auto_confirm` (supprime des MP4 !), `/restore`, `/update_weight`, `/clear_logs`. Ajouter le contrôle (un décorateur `login_required` suffit).
- [ ] XSS : le JS fait `l.innerHTML = '<span class="msg">' + d.text + '</span>'`. Les logs contiennent des entrées utilisateur (nom d'utilisateur d'un login raté, URL). Utiliser `textContent`.
- [ ] Actions destructrices en GET (`/auto_confirm`, `/restore`, `/clear_logs`) : passer en POST.
- [ ] Garder l'écoute sur `127.0.0.1` (défaut actuel de `socketio.run`) ; ne pas exposer sur le réseau.

## Phase 1 — Fiabilité / bugs

- [ ] **Bug** `/create` : `catcore = request.form.get("catcore") == "off"`. Une checkbox cochée envoie `"on"` → le mode Catcore est inversé. Corriger en `== "on"`.
- [ ] `creator.py` : nom de sortie `index = len(glob("*.mp4")) + 1` → écrase des fichiers si un fichier a été supprimé ou si deux jobs tournent en parallèle. Utiliser un horodatage ou un uuid court.
- [ ] `creator.py` : le clip séparateur (`SEPARATOR_NAME`) n'est pas redimensionné en 1080x1920 avant `concatenate_videoclips`. Lui appliquer le même traitement que les autres clips.
- [ ] `creator.py` : les `VideoFileClip` d'origine (`selected`) et `separator_clip` ne sont jamais fermés → fuite de handles ffmpeg sur les gros batchs. Fermer dans un `finally`.
- [ ] `creator.py` : un clip sans piste audio fait planter ou produit une vidéo muette selon les cas. Vérifier `clip.audio is None`. Cela compte pour la Phase 3 (le mixage audio suppose une piste audio, ou un silence à générer).
- [ ] Jobs concurrents : rien n'empêche de lancer 3 batchs en même temps (CPU saturé, collisions). Ajouter une file simple (un seul worker thread + `queue.Queue`) avec un état visible dans l'UI : en attente / en cours / terminé / erreur.
- [ ] Progression : moviepy accepte un `logger` proglog. Brancher un logger custom qui émet le pourcentage via Socket.IO (`job_progress`).
- [ ] `/choose_output` et `/choose_input` ouvrent une fenêtre Tkinter depuis un thread Flask. C'est fragile (Tk n'est pas thread-safe, la fenêtre peut s'ouvrir derrière le navigateur). Garder pour l'instant, mais `input_folder` **n'est utilisé nulle part** : `/restore` scanne toujours `BASE_DIR / "clips"`. Soit l'utiliser, soit retirer le bouton.
- [ ] Persister `output_folder` / `input_folder` dans `data/settings.json` (aujourd'hui ils sont perdus au redémarrage).
- [ ] Valider les entrées de formulaire (`int(...)`/`float(...)` lèvent une 500 sur une valeur vide).
- [ ] Ajouter quelques tests `pytest` sur la logique pure : sélection des clips selon `cible`/`marge`, construction de la commande ffmpeg de mixage (Phase 3), `render_template`.

## Phase 2 — UI

Garder la direction visuelle actuelle (dark, violet `#7c3aed` / cyan `#06b6d4`, Space Mono + DM Sans) : elle est cohérente. Le travail porte sur la structure et les manques.

- [ ] Sortir le HTML de `main.py` : `templates/` (Jinja `base.html` + une page par section) et `static/` (CSS/JS). Pas de framework front, pas de build.
- [ ] Remplacer les emoji d'icônes par des SVG inline (Lucide par exemple) ; ajouter `aria-label` aux boutons-icônes ; focus visible ; contraste ≥ 4.5:1 (le gris `--muted #64748b` sur `#08080e` est limite pour le texte 9-10 px : monter la taille min à 12 px).
- [ ] Nouvelle section **Bibliothèque** : grille des MP4 du dossier de sortie, aperçu `<video>` (servi par une route qui utilise `send_from_directory` sur `output_folder` uniquement — pas de chemin libre fourni par le client), durée, date, actions : Éditer / Publier sur TikTok / Supprimer.
- [ ] Panneau **Jobs** (remplace ou complète la barre de logs) : liste des jobs avec barre de progression.
- [ ] Toasts pour succès/erreur au lieu de devoir lire les logs.
- [ ] Section **Comptes** : état de connexion TikTok (Phase 4).

## Phase 3 — Éditeur simple (type CapCut, version minimale)

Besoin exprimé par l'utilisateur : **ajouter une musique de fond, baisser le son de la vidéo, mettre la musique plus fort.**

Périmètre v1 :
- choisir une vidéo depuis la Bibliothèque ;
- importer un fichier musique (mp3/m4a/wav) ou en choisir un dans `assets/music/` (dossier déjà ignoré par git via `assets/*`) ;
- slider **volume vidéo** (0–150 %) et slider **volume musique** (0–150 %) ;
- **décalage de départ** dans la musique (commencer à 0:30 par exemple) ;
- fondu de sortie de la musique (on/off, 1–3 s) ;
- la musique boucle si elle est plus courte que la vidéo et est coupée à la fin de la vidéo ;
- aperçu dans le navigateur **avant** rendu : `<video>` + `<audio>` synchronisés en JS (pas de rendu serveur pour prévisualiser). Attention : `HTMLMediaElement.volume` n'accepte que 0–1 (`IndexSizeError` au-delà). Passer par Web Audio (`createMediaElementSource` + `GainNode`) pour un gain > 100 % ;
- bouton **Exporter** → nouveau fichier `<nom>_edit_<timestamp>.mp4`, l'original n'est jamais écrasé.

Implémentation recommandée : appel direct à ffmpeg (binaire fourni par `imageio_ffmpeg.get_ffmpeg_exe()`, déjà installé), **sans réencoder la vidéo** (`-c:v copy`) → quelques secondes au lieu de plusieurs minutes avec moviepy. Mettre ça dans un nouveau module `core/editor.py`.

```
ffmpeg -y -i video.mp4 -ss <offset> -stream_loop -1 -i music.mp3 \
  -filter_complex "[0:a]volume=<vv>[a0];[1:a]volume=<mv>,afade=t=out:st=<dur-fade>:d=<fade>[a1];[a0][a1]amix=inputs=2:duration=first:dropout_transition=0[aout]" \
  -map 0:v -map "[aout]" -c:v copy -c:a aac -shortest out.mp4
```

Cas à gérer :
- vidéo **sans piste audio** : remplacer `[0:a]` par une source `anullsrc`, ou mapper seulement la musique ;
- `amix` divise le volume par le nombre d'entrées → compenser (`amix=...:normalize=0` si la version de ffmpeg le supporte — **non vérifié**, tester avec `ffmpeg -h filter=amix` sur le binaire imageio-ffmpeg 0.4.9 ; sinon multiplier les volumes) ; vérifier à l'oreille ;
- lancer ffmpeg avec une **liste d'arguments** (`subprocess.run([...])`), jamais une chaîne shell (noms de fichiers fournis par l'utilisateur) ;
- fichiers uploadés : `werkzeug.utils.secure_filename`, extension en liste blanche, taille max (`MAX_CONTENT_LENGTH`).

v2 probable (l'utilisateur a cité la musique « par exemple » et veut un montage façon CapCut — **confirmer avec lui** avant de figer le périmètre) : couper / réordonner les clips sur une timeline simple, texte et sous-titres (`core/scene.py` et la transcription pourraient servir). Plus loin : filtres, transitions.

Note droits d'auteur : une musique commerciale ajoutée au fichier peut faire couper le son ou bloquer la vidéo sur TikTok. Le signaler dans l'UI ; préférer des musiques libres de droits.

## Phase 4 — Connexion TikTok depuis l'UI

`core/uploader.py::upload_tiktok` implémente déjà init → upload → poll avec l'API officielle Content Posting. Il manque l'OAuth et plusieurs points imposés par TikTok.

Prérequis côté utilisateur (manuel, hors code) :
1. créer une app sur https://developers.tiktok.com, ajouter les produits **Login Kit** et **Content Posting API** ;
2. scopes : `user.info.basic`, `video.upload`, `video.publish` ;
3. déclarer l'URL de redirection. **À vérifier** dans la doc actuelle si `http://localhost:5000/...` est accepté pour une app web ou s'il faut déclarer l'app en type Desktop (PKCE). Sinon il faudra un tunnel HTTPS.
4. tant que l'app **n'est pas auditée par TikTok**, les publications via l'API sont limitées à la visibilité privée (`SELF_ONLY`). L'utilisateur la passe en public depuis l'app TikTok. L'UI doit le dire clairement.

À coder :
- [ ] Routes `/tiktok/connect` (redirige vers `https://www.tiktok.com/v2/auth/authorize/` avec `client_key`, `scope`, `redirect_uri`, `state` aléatoire stocké en session, + PKCE si requis) et `/tiktok/callback` (vérifie `state`, échange le `code` sur `https://open.tiktokapis.com/v2/oauth/token/`).
- [ ] Stocker `access_token` (~24 h), `refresh_token` (~1 an), `open_id`, dates d'expiration dans `data/tiktok_token.json` (ignoré par git), pas dans `.env`. Rafraîchir automatiquement le token avant chaque upload s'il est expiré.
- [ ] Bouton Déconnecter (révocation via `/v2/oauth/revoke/` + suppression du fichier).
- [ ] Avant chaque publication, appeler `/v2/post/publish/creator_info/query/` (exigé par TikTok) : il renvoie les `privacy_level_options` autorisées, le nom du compte et la durée max. Proposer seulement ces options dans le formulaire. Aujourd'hui `upload_tiktok` force `PUBLIC_TO_EVERYONE`, ce qui échoue pour une app non auditée.
- [ ] Formulaire de publication (depuis la Bibliothèque) : titre/légende, confidentialité, autoriser commentaires/duo/stitch. TikTok exige que ces choix soient faits explicitement par l'utilisateur dans l'UI.
- [ ] Upload par morceaux : aujourd'hui `chunk_size = file_size` et un seul chunk, et tout le fichier est lu en mémoire. Respecter les limites de taille de chunk de la doc (fichiers volumineux → plusieurs chunks) et lire en streaming.
- [ ] Alternative à proposer en option : `source: "FILE_UPLOAD"` vers l'endpoint **inbox** (`/v2/post/publish/inbox/video/init/`, scope `video.upload`). La vidéo arrive en brouillon dans l'app TikTok et l'utilisateur la publie lui-même. C'est plus simple à faire valider par TikTok et ça respecte l'anti-objectif.
- [ ] Brancher `core/scheduler.py` seulement après que la publication manuelle fonctionne (route `/schedule` + `init_scheduler` au démarrage).

## Ordre conseillé

Phase 0 → Phase 1 (bugs + file de jobs) → Phase 2 (templates + Bibliothèque) → Phase 3 (éditeur audio) → Phase 4 (TikTok).
La Bibliothèque (Phase 2) est le point d'entrée commun de l'éditeur et de la publication : la faire avant les phases 3 et 4.

## Décisions prises / laissées au builder

- Choix de l'agent (non validé explicitement par l'utilisateur) : rester sur Flask + Jinja + JS vanilla, pas de React ; mixage audio via ffmpeg direct plutôt que moviepy ; tokens TikTok dans `data/` plutôt que `.env`.
- Laissé au builder : lib d'icônes SVG, mise en page exacte de l'éditeur, format exact de la file de jobs.
