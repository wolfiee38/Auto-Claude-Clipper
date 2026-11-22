"""Render vertical Reels from a job file. Run by GitHub Actions on every push to jobs/.
Outputs: media/<job name>/<out>.mp4  (skips clips that already exist)

Job fields: name, source_url, bottom_text, clips[{out,start,end,top,layout?}]
Optional "source_type": "youtube" -> each clip's section is fetched with yt-dlp
(only START-END is downloaded) and rendered from 0. Otherwise source_url is
downloaded once with urllib (original behavior).
Optional per-clip "layout": "full" (default) = blurred background + centered 16:9 frame.
"""
import json, os, shutil, subprocess, sys, urllib.request, glob

FONT = next((f for f in [
    "/usr/share/fonts/opentype/inter/Inter-Black.otf",
    "/usr/share/fonts/truetype/inter/Inter-Black.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
] if os.path.exists(f)), None) or glob.glob("/usr/share/fonts/**/*Bold*.ttf", recursive=True)[0]
print("font:", FONT)

_YTDLP_READY = False
LOG = []
# player clients to try in order; YouTube often blocks datacenter IPs for some clients only
YT_CLIENTS = [None, "tv,web_safari", "mweb", "android_vr"]


def ensure_ytdlp():
    global _YTDLP_READY
    if not _YTDLP_READY:
        print("installing yt-dlp...")
        base = [sys.executable, "-m", "pip", "install", "-q", "-U", "yt-dlp[default]"]
        if subprocess.run(base).returncode != 0:  # Ubuntu 24.04 system python is "externally managed"
            subprocess.run(base + ["--break-system-packages"], check=True)
        _YTDLP_READY = True


def yt_info(url):
    try:
        r = subprocess.run([sys.executable, "-m", "yt_dlp", "--no-warnings", "--skip-download",
                            "--print", "%(title)s | duration=%(duration)s | live=%(is_live)s | was_live=%(was_live)s", url],
                           capture_output=True, text=True, timeout=180)
        msg = "yt info: " + (r.stdout or "").strip() + " " + (r.stderr or "").strip()[-800:]
        print(msg); LOG.append(msg)
    except Exception as e:
        print("yt info failed:", e)


def yt_section(url, st, en, dest):
    """Download only [st, en] seconds of a YouTube video to dest. Returns True on success."""
    for client in YT_CLIENTS:
        for f in glob.glob(dest + "*"):
            os.remove(f)
        cmd = [sys.executable, "-m", "yt_dlp", "--no-warnings", "--no-playlist",
               "--download-sections", f"*{st}-{en}", "--force-keyframes-at-cuts",
               "-f", "bv*[height<=1080]+ba/b", "--merge-output-format", "mp4",
               "-o", dest, url]
        if shutil.which("node"):
            cmd[3:3] = ["--js-runtimes", "node"]
        if client:
            cmd[3:3] = ["--extractor-args", f"youtube:player_client={client}"]
        print("yt-dlp section", st, en, "client", client or "default")
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(dest) and os.path.getsize(dest) > 10000:
            print("section bytes:", os.path.getsize(dest)); LOG.append(f"ok [{st}-{en} client={client}] {os.path.getsize(dest)} bytes")
            return True
        msg = f"yt-dlp failed [{st}-{en} client={client}]: " + (r.stderr or r.stdout or "").strip()[-1500:]
        print(msg); LOG.append(msg)
    return False


def build_fc(c, bottom, dur, layout):
    fo = max(dur - 0.4, 0)
    draw_top = f"drawtext=fontfile='{FONT}':textfile=/tmp/top.txt:fontsize=64:fontcolor=white:x=(w-text_w)/2:y=330:box=1:boxcolor=black@0.55:boxborderw=24:line_spacing=10:text_align=C,"
    draw_bot = (f"drawtext=fontfile='{FONT}':textfile=/tmp/bot.txt:fontsize=58:fontcolor=0xFFD24A:x=(w-text_w)/2:y=1420:box=1:boxcolor=black@0.55:boxborderw=22:line_spacing=12:text_align=C," if bottom else "")
    # layout "full" (default): blurred background + full 16:9 frame centered
    return (
        "[0:v]fps=30,split=2[a][b];"
        "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:3,eq=brightness=-0.12[bg];"
        "[b]scale=1080:-2:flags=lanczos,eq=contrast=1.06:saturation=1.08[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,"
        + (draw_top if c.get("top") else "") + draw_bot +
        "format=yuv420p[v];"
        f"[0:a]afade=t=in:d=0.25,afade=t=out:st={fo:.2f}:d=0.4,loudnorm=I=-14:TP=-1.5[au]"
    )


def encode(src, out, fc, ss=None, to=None):
    cmd = ["ffmpeg", "-loglevel", "error", "-y"]
    if ss is not None:
        cmd += ["-ss", str(ss), "-to", str(to)]
    cmd += ["-i", src, "-filter_complex", fc, "-map", "[v]", "-map", "[au]", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "21", "-profile:v", "high", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out]
    subprocess.run(cmd, check=True)
    print("rendered", out, os.path.getsize(out))


def run_job(path):
    job = json.load(open(path))
    outdir = os.path.join("media", job["name"])
    os.makedirs(outdir, exist_ok=True)
    todo = [c for c in job["clips"] if not os.path.exists(os.path.join(outdir, c["out"] + ".mp4"))]
    if not todo:
        print("nothing to do for", path); return
    bottom = job.get("bottom_text", "")
    open("/tmp/bot.txt", "w").write(bottom)
    is_yt = job.get("source_type") == "youtube"

    src = None
    if is_yt:
        ensure_ytdlp()
        yt_info(job["source_url"])
    else:
        src = "/tmp/src_" + job["name"] + ".mp4"
        if not os.path.exists(src):
            print("downloading source...")
            req = urllib.request.Request(job["source_url"], headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req) as r, open(src, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
            print("source bytes:", os.path.getsize(src))

    failed = []
    for c in todo:
        open("/tmp/top.txt", "w").write(c.get("top", ""))
        st, en = float(c["start"]), float(c["end"])
        layout = c.get("layout", "full")
        fc = build_fc(c, bottom, en - st, layout)
        out = os.path.join(outdir, c["out"] + ".mp4")
        if is_yt:
            sec = f"/tmp/yt_{job['name']}_{c['out']}.mp4"
            if not yt_section(job["source_url"], st, en, sec):
                failed.append(c["out"]); continue
            try:
                encode(sec, out, fc, 0, en - st)
            except subprocess.CalledProcessError as e:
                print("ffmpeg failed for", c["out"], e); failed.append(c["out"])
        else:
            encode(src, out, fc, st, en)
    if failed:
        print("FAILED clips in", path, ":", failed)
    if is_yt:  # commit a small log so failures are visible without Actions log access
        with open(os.path.join(outdir, "_render_log.txt"), "w") as f:
            f.write("failed: %s\n\n" % failed + "\n".join(LOG[-60:]))
    return failed


if __name__ == "__main__":
    jobs = sys.argv[1:] or sorted(glob.glob("jobs/*.json"))
    for j in jobs:
        run_job(j)
