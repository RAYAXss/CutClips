import os
import json
import threading
import datetime
import webbrowser
from pathlib import Path

from flask import Flask, render_template_string, request, redirect, session
from flask_socketio import SocketIO
from werkzeug.security import generate_password_hash, check_password_hash
import tkinter as tk
from tkinter.filedialog import askdirectory

# ================= CORE =================
from core.downloader import download_video
from core.creator import create_video_batch
from core.restore import restaurer_clips_json
from core.categories import load_weights, save_weights

# ================= PATH =================
BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
CLIPS_JSON = DATA_DIR / "clips_info.json"
DEFAULT_OUTPUT = BASE_DIR / "output"
USERS_JSON = DATA_DIR / "users.json"

for d in (DATA_DIR, DEFAULT_OUTPUT):
    d.mkdir(parents=True, exist_ok=True)

if not USERS_JSON.exists():
    with open(USERS_JSON, "w") as f:
        json.dump({"admin": generate_password_hash("admin")}, f)

# ================= APP =================
app = Flask(__name__)
app.secret_key = "cliptwist_secret"
socketio = SocketIO(app)

logs = []
output_folder = str(DEFAULT_OUTPUT)

def log(message):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    formatted = f"[{ts}] {message}"
    logs.append(formatted)
    socketio.emit("new_log", formatted)
    print(formatted)

def is_logged():
    return "user" in session




# ================= AUTO WEEKLY =================
def auto_launch():
    log("Auto Weekly started...")
    mp4_files = [f for f in os.listdir(output_folder) if f.lower().endswith(".mp4")]

    if len(mp4_files) >= 6:
        for f in mp4_files:
            os.remove(os.path.join(output_folder, f))
        log("Old videos deleted")

    create_video_batch(6, "final", True, "funny", log, 70, 5, False, output_folder)
    log("6 videos created")

# ================= ROUTES =================

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        with open(USERS_JSON) as f:
            users = json.load(f)

        u = request.form["username"]
        p = request.form["password"]

        if u in users and check_password_hash(users[u], p):
            session["user"] = u
            log("ClipTwist launched")
            return redirect("/section/creation")

        return "Invalid credentials"

    return LOGIN_HTML

@app.route("/choose_output")
def choose_output():
    global output_folder

    if not is_logged():
        return redirect("/login")

    def select_folder():
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        folder = askdirectory(initialdir=output_folder)
        root.destroy()
        return folder

    folder = select_folder()

    if folder:
        output_folder = folder
        log(f"Output folder changed to: {folder}")

    return redirect("/section/creation")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/section/<name>")
def section(name):
    if not is_logged():
        return redirect("/login")

    return render_template_string(
        DASHBOARD_HTML,
        active=name,
        weights=load_weights(),
        logs=logs
    )

@app.route("/download", methods=["POST"])
def download():
    url = request.form["url"]
    cat = request.form.get("category") or "funny"
    threading.Thread(target=lambda: download_video(url, log, cat)).start()
    return redirect("/section/download")

@app.route("/create", methods=["POST"])
def create():
    nb = int(request.form["number"])
    cat = request.form["category"]
    cible = float(request.form["cible"])
    marge = float(request.form["marge"])
    catcore = request.form.get("catcore") == "on"

    threading.Thread(target=lambda: create_video_batch(
        nb, "final", True, cat, log, cible, marge, catcore, output_folder
    )).start()

    return redirect("/section/creation")

@app.route("/auto_confirm")
def auto_confirm():
    threading.Thread(target=auto_launch).start()
    return redirect("/section/creation")

@app.route("/restore")
def restore():
    threading.Thread(target=lambda: restaurer_clips_json(
        log, str(CLIPS_JSON), str(BASE_DIR / "clips")
    )).start()
    return redirect("/section/creation")

@app.route("/update_weight", methods=["POST"])
def update_weight():
    cat = request.form["category"]
    val = float(request.form["value"])

    weights = load_weights()
    weights[cat] = val
    save_weights(weights)
    log(f"Weight updated: {cat} = {val}")

    return redirect("/section/stats")

@app.route("/clear_logs")
def clear_logs():
    logs.clear()
    return redirect("/section/creation")

# ================= HTML =================

LOGIN_HTML = """
<!DOCTYPE html>
<html>
<head>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-dark d-flex justify-content-center align-items-center vh-100">

<form method="POST" class="bg-secondary p-4 rounded text-white" style="width:300px;">
<h4 class="text-center">ClipTwist</h4>
<input class="form-control mb-3" name="username" placeholder="Username">
<input type="password" class="form-control mb-3" name="password" placeholder="Password">
<button class="btn btn-primary w-100">Login</button>
</form>

</body>
</html>
"""

DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
<title>ClipTwist</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
<script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
<style>
body { background:#121212; color:white; height:100vh; display:flex; flex-direction:column; }
.navbar { background:#0d0d0d; }
.section-container { flex:1; display:flex; justify-content:center; align-items:center; }
.section-frame { background:#1e1e1e; padding:40px; border-radius:15px; width:500px; box-shadow:0 0 20px rgba(0,0,0,0.5); }
.logbox { background:black; color:#00ff00; height:180px; overflow:auto; padding:10px; }
</style>
</head>
<body>

<nav class="navbar navbar-expand-lg px-4">
<a class="navbar-brand text-white">ClipTwist</a>

<div class="navbar-nav">
<a class="nav-link text-white" href="/section/download">Download</a>
<a class="nav-link text-white" href="/section/creation">Creation</a>
<a class="nav-link text-white" href="/section/stats">Stats</a>
</div>

<div class="ms-auto d-flex gap-2">
<a href="/choose_output" class="btn btn-info btn-sm">Choose Output</a>
<button class="btn btn-warning btn-sm" data-bs-toggle="modal" data-bs-target="#autoModal">
Auto Weekly
</button>
<a href="/clear_logs" class="btn btn-secondary btn-sm">Clear logs</a>
<a href="/logout" class="btn btn-danger btn-sm">Logout</a>
</div>

</nav>

<div class="section-container">

<div class="section-frame text-center">

{% if active == 'download' %}
<h3>Download</h3>
<form method="POST" action="/download">
<input name="url" class="form-control mb-2" placeholder="Video URL">
<input name="category" class="form-control mb-2" placeholder="Category">
<button class="btn btn-primary w-100">Download</button>
</form>
{% endif %}

{% if active == 'creation' %}
<h3>Create Videos</h3>
<form method="POST" action="/create">
<input type="number" name="number" value="1" class="form-control mb-2">
<input name="category" class="form-control mb-2">
<input type="number" name="cible" value="70" class="form-control mb-2">
<input type="number" name="marge" value="5" class="form-control mb-2">
<div class="form-check text-start mb-2">
<input class="form-check-input" type="checkbox" name="catcore">
<label class="form-check-label">Add Catcore between clips</label>
</div>
<button class="btn btn-success w-100">Create</button>
</form>
<a href="/restore" class="btn btn-outline-light w-100 mt-2">Restore JSON</a>
{% endif %}

{% if active == 'stats' %}
<h3>Stats</h3>
{% for cat, val in weights.items() %}
<form method="POST" action="/update_weight" class="d-flex mb-2">
<div class="flex-grow-1 text-start">{{cat}}</div>
<input type="number" step="0.01" name="value" value="{{val}}" class="form-control mx-2">
<input type="hidden" name="category" value="{{cat}}">
<button class="btn btn-warning btn-sm">Update</button>
</form>
{% endfor %}
{% endif %}

</div>
</div>

<div class="logbox" id="logbox">
{% for l in logs %}
<div>{{l}}</div>
{% endfor %}
</div>
<!-- MODAL -->
<div class="modal fade" id="autoModal" tabindex="-1">
<div class="modal-dialog">
<div class="modal-content bg-dark text-white">
<div class="modal-header">
<h5 class="modal-title">Confirm Auto Weekly</h5>
<button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
</div>
<div class="modal-body">
Are you sure you want to launch Auto Weekly?<br>
Old videos in output folder will be deleted.
</div>
<div class="modal-footer">
<button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
<a href="/auto_confirm" class="btn btn-warning">Yes, launch</a>
</div>
</div>
</div>
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>

<script>
var socket = io();
socket.on("new_log", function(data){
    var box = document.getElementById("logbox");
    box.innerHTML += "<div>"+data+"</div>";
    box.scrollTop = box.scrollHeight;
});
</script>

</body>
</html>
"""

def open_browser():
    webbrowser.open("http://127.0.0.1:5000/login")

if __name__ == "__main__":
    threading.Timer(1, open_browser).start()
    socketio.run(app, debug=False)
