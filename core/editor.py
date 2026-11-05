# core/editor.py
"""
Simple editor: background music, video/music volume, timed text overlays.
Calls the ffmpeg binary shipped with imageio-ffmpeg directly (no moviepy):
audio-only edits copy the video stream, so export takes seconds.
"""

import os
import re
import subprocess
import tempfile
import textwrap
import uuid
import datetime
from pathlib import Path

import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

MUSIC_EXTS = {".mp3", ".m4a", ".wav", ".aac", ".ogg"}
POSITIONS = {
    "top":    "h*0.12",
    "center": "(h-text_h)/2",
    "bottom": "h*0.78-text_h",
}
COLORS = {"white": "white", "yellow": "#facc15", "black": "black"}
FONT_CANDIDATES = [
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def _fontfile():
    return next((f for f in FONT_CANDIDATES if os.path.exists(f)), None)


def _esc(path: str) -> str:
    """Escape a path for use as a quoted filtergraph option value (Windows drive colon)."""
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def probe(path: str) -> dict:
    """Duration (s) and whether the file has an audio stream, parsed from `ffmpeg -i`."""
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return parse_probe(r.stderr)


def parse_probe(stderr: str) -> dict:
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr)
    duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0
    size = re.search(r"Stream #\S+.*Video:.*?(\d{2,5})x(\d{2,5})", stderr)
    return {"duration": duration, "has_audio": bool(re.search(r"Stream #\S+.*Audio:", stderr)),
            "width": int(size.group(1)) if size else 1080}


def wrap_text(text: str, width_px: int, font_size: int) -> str:
    """drawtext never wraps: break lines so ~90% of the frame width is used (Arial ~0.55em/char)."""
    max_chars = max(8, int(width_px * 0.9 / (font_size * 0.55)))
    return "\n".join(textwrap.fill(line, max_chars) for line in str(text).splitlines() or [""])


def build_args(video: str, out: str, duration: float, has_audio: bool,
               music: str = None, video_vol: float = 1.0, music_vol: float = 1.0,
               music_offset: float = 0.0, fade: float = 0.0,
               texts: list = None, text_files: list = None, font: str = None) -> list:
    """
    Pure: build the ffmpeg argument list.
    texts: [{"start", "end", "position", "size", "color"}], text_files: matching UTF-8 files
    holding each text (avoids filtergraph escaping of user text).
    """
    texts = texts or []
    args = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1", "-i", str(video)]
    if music:
        args += ["-ss", f"{max(music_offset, 0):.3f}", "-stream_loop", "-1", "-i", str(music)]

    graph, vmap, amap = [], "0:v", None

    if texts:
        chain = "[0:v]"
        for i, (t, tf) in enumerate(zip(texts, text_files or [])):
            opts = [
                f"textfile='{_esc(tf)}'", "expansion=none", "reload=0",
                f"fontsize={int(t.get('size', 72))}",
                f"fontcolor={COLORS.get(t.get('color'), 'white')}",
                "borderw=4", "bordercolor=black@0.85",
                "x=(w-text_w)/2", f"y={POSITIONS.get(t.get('position'), POSITIONS['bottom'])}",
                f"enable='between(t,{float(t.get('start', 0)):.3f},{float(t.get('end', duration)):.3f})'",
            ]
            if font:
                opts.insert(0, f"fontfile='{_esc(font)}'")
            label = f"[v{i}]"
            graph.append(f"{chain}drawtext={':'.join(opts)}{label}")
            chain = label
        vmap = chain

    if music:
        m = f"[1:a]volume={music_vol:.3f}"
        if fade > 0 and duration > fade:
            m += f",afade=t=out:st={duration - fade:.3f}:d={fade:.3f}"
        if has_audio:
            graph.append(f"[0:a]volume={video_vol:.3f}[a0]")
            graph.append(m + "[a1]")
            # amix halves each input on ffmpeg 4.2 (no normalize=0): compensate, then limit clipping
            graph.append("[a0][a1]amix=inputs=2:duration=first:dropout_transition=0,volume=2,alimiter=limit=0.95[aout]")
        else:
            graph.append(m + ",alimiter=limit=0.95[aout]")
        amap = "[aout]"
    elif has_audio and abs(video_vol - 1.0) > 1e-6:
        graph.append(f"[0:a]volume={video_vol:.3f},alimiter=limit=0.95[aout]")
        amap = "[aout]"
    elif has_audio:
        amap = "0:a"

    if graph:
        args += ["-filter_complex", ";".join(graph)]
    args += ["-map", vmap]
    if amap:
        args += ["-map", amap]

    if texts:
        args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
    else:
        args += ["-c:v", "copy"]
    if amap == "0:a":
        args += ["-c:a", "copy"]
    elif amap:
        args += ["-c:a", "aac", "-b:a", "192k"]
    args += ["-t", f"{duration:.3f}", "-movflags", "+faststart", str(out)]
    return args


def export(video: str, output_folder: str, log, progress=None, music: str = None,
           video_vol: float = 1.0, music_vol: float = 1.0, music_offset: float = 0.0,
           fade: float = 0.0, texts: list = None):
    """Render the edit to <stem>_edit_<timestamp>.mp4. The original is never touched."""
    info = probe(video)
    if not info["duration"]:
        log(f"Éditeur : durée illisible pour {video}", "error")
        return None
    duration = info["duration"]
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(output_folder) / f"{Path(video).stem}_edit_{stamp}_{uuid.uuid4().hex[:4]}.mp4"

    texts = [t for t in (texts or []) if str(t.get("text", "")).strip()]
    with tempfile.TemporaryDirectory() as tmp:
        text_files = []
        for i, t in enumerate(texts):
            tf = Path(tmp) / f"t{i}.txt"
            tf.write_text(wrap_text(t["text"], info["width"], int(t.get("size", 72))), encoding="utf-8")
            text_files.append(str(tf))
        args = build_args(video, str(out), duration, info["has_audio"], music, video_vol, music_vol,
                          music_offset, fade, texts, text_files, _fontfile() if texts else None)
        log(f"Éditeur : export de {Path(video).name}...", "info")
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace")
        for line in proc.stdout:
            m = re.match(r"out_time=(\d+):(\d+):(\d+(?:\.\d+)?)", line.strip())
            if m and progress:
                t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
                progress(t / duration * 100)
        err = proc.stderr.read()
        proc.wait()

    if proc.returncode != 0 or not out.exists():
        log(f"Éditeur : ffmpeg a échoué — {err.strip().splitlines()[-1] if err.strip() else proc.returncode}", "error")
        out.unlink(missing_ok=True)
        return None
    log(f"Éditeur : vidéo exportée {out.name}", "success")
    return str(out)
