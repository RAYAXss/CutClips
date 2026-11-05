import subprocess
import time

import pytest

from core import editor, tiktok
from core.creator import select_clips
from core.uploader import render_template


# ── creator ──

def test_select_clips_stays_in_window():
    clips = [{"clip_path": f"c{i}", "duration": d} for i, d in enumerate([30, 50, 20, 15, 0.2, 10])]
    sel = select_clips(clips, cible=60, marge=5)
    total = sum(c["duration"] for c in sel)
    assert 55 <= total <= 65
    assert all(c["duration"] >= 0.5 for c in sel)


def test_select_clips_none_fit():
    assert select_clips([{"clip_path": "a", "duration": 500}], 60, 5) == []


def test_render_template():
    out = render_template("{{platform}} {{filename}}", "tiktok", "/x/funny_1.mp4")
    assert out == "tiktok funny_1"


# ── editor (pure) ──

def test_parse_probe():
    err = ("  Duration: 00:01:10.05, start: 0.0\n"
           "    Stream #0:0(und): Video: h264 (High), yuv420p, 1080x1920, 30 fps\n"
           "    Stream #0:1(und): Audio: aac (LC), 44100 Hz, stereo\n")
    assert editor.parse_probe(err) == {"duration": 70.05, "has_audio": True, "width": 1080}


def test_build_args_audio_only_copies_video():
    a = editor.build_args("in.mp4", "out.mp4", 10, True, music="m.mp3", video_vol=0.3, music_vol=1.2, fade=2)
    g = a[a.index("-filter_complex") + 1]
    assert "[0:a]volume=0.300" in g and "volume=1.200" in g and "afade=t=out:st=8.000:d=2.000" in g
    assert "amix=inputs=2" in g
    assert a[a.index("-c:v") + 1] == "copy"
    assert a[a.index("-t") + 1] == "10.000"
    assert a[a.index("-stream_loop") + 1] == "-1"


def test_build_args_no_audio_track_uses_music_only():
    a = editor.build_args("in.mp4", "out.mp4", 10, False, music="m.mp3")
    g = a[a.index("-filter_complex") + 1]
    assert "[0:a]" not in g and "amix" not in g


def test_build_args_text_reencodes_and_escapes_paths():
    a = editor.build_args("in.mp4", "out.mp4", 10, True,
                          texts=[{"start": 1, "end": 3, "position": "top", "size": 60, "color": "yellow"}],
                          text_files=["C:\\tmp\\t0.txt"], font="C:/Windows/Fonts/arial.ttf")
    g = a[a.index("-filter_complex") + 1]
    assert "textfile='C\\:/tmp/t0.txt'" in g and "fontfile='C\\:/Windows/Fonts/arial.ttf'" in g
    assert "between(t,1.000,3.000)" in g
    assert a[a.index("-c:v") + 1] == "libx264"
    assert a[a.index("-c:a") + 1] == "copy"


def test_wrap_text():
    assert all(len(line) <= 25 for line in editor.wrap_text("mot " * 30, 1080, 72).split("\n"))


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    run = lambda *a: subprocess.run([editor.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *a], check=True)
    run("-f", "lavfi", "-i", "testsrc=size=320x568:rate=30:duration=3", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=3", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(d / "v.mp4"))
    run("-f", "lavfi", "-i", "testsrc=size=320x568:rate=30:duration=3", "-c:v", "libx264", str(d / "mute.mp4"))
    run("-f", "lavfi", "-i", "sine=frequency=880:duration=1", str(d / "m.m4a"))
    return d


def _mean_db(path):
    r = subprocess.run([editor.FFMPEG, "-hide_banner", "-i", path, "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True)
    return float(r.stderr.split("mean_volume:")[1].split("dB")[0])


def test_export_keeps_level_and_never_overwrites(media):
    src = str(media / "v.mp4")
    out = editor.export(src, str(media), lambda *a: None, music=str(media / "m.m4a"), video_vol=1, music_vol=0)
    assert out and out != src
    assert abs(_mean_db(out) - _mean_db(src)) < 1.5     # amix compensation keeps 100 % at 100 %


def test_export_silent_video_with_music_and_text(media):
    out = editor.export(str(media / "mute.mp4"), str(media), lambda *a: None, music=str(media / "m.m4a"),
                        fade=1, texts=[{"text": "l'été: 100% \\o/", "start": 0, "end": 2}])
    info = editor.probe(out)
    assert info["has_audio"] and abs(info["duration"] - 3) < 0.3


# ── tiktok (pure) ──

def test_pkce_is_hex_sha256():
    v, c = tiktok.pkce_pair()
    assert 43 <= len(v) <= 128 and len(c) == 64 and int(c, 16) >= 0


def test_authorize_url_has_required_params(monkeypatch):
    monkeypatch.setenv("TIKTOK_CLIENT_KEY", "abc")
    url = tiktok.authorize_url("st", "ch")
    for part in ("client_key=abc", "response_type=code", "state=st", "code_challenge=ch",
                 "code_challenge_method=S256", "redirect_uri=http%3A%2F%2F127.0.0.1%3A5000%2Ftiktok%2Fcallback%2F"):
        assert part in url


def test_needs_refresh():
    now = time.time()
    assert tiktok.needs_refresh({"expires_at": now + 60}, now)
    assert not tiktok.needs_refresh({"expires_at": now + 3600}, now)


@pytest.mark.parametrize("size,expect", [
    (4 * tiktok.MB, (4 * tiktok.MB, 1)),                     # < 5 MB: whole file
    (8 * tiktok.MB, (8 * tiktok.MB, 1)),                     # fits in one chunk
    (35 * tiktok.MB + 123, (10 * tiktok.MB, 3)),             # remainder merged into last chunk
    (700 * tiktok.MB, (10 * tiktok.MB, 70)),
])
def test_chunk_plan(size, expect):
    chunk, count = tiktok.chunk_plan(size)
    assert (chunk, count) == expect
    last = size - chunk * (count - 1)
    assert last <= 128 * tiktok.MB and (count == 1 or chunk >= 5 * tiktok.MB)
