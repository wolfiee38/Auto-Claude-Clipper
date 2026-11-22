"""Render vertical Reels from a job file. Run by GitHub Actions on every push to jobs/.
Outputs: media/<job name>/<out>.mp4  (skips clips that already exist)
"""
import json, os, subprocess, sys, urllib.request, glob

FONT = next((f for f in [
    "/usr/share/fonts/opentype/inter/Inter-Black.otf",
    "/usr/share/fonts/truetype/inter/Inter-Black.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
] if os.path.exists(f)), None) or glob.glob("/usr/share/fonts/**/*Bold*.ttf", recursive=True)[0]
print("font:", FONT)


def run_job(path):
    job = json.load(open(path))
    outdir = os.path.join("media", job["name"])
    os.makedirs(outdir, exist_ok=True)
    todo = [c for c in job["clips"] if not os.path.exists(os.path.join(outdir, c["out"] + ".mp4"))]
    if not todo:
        print("nothing to do for", path); return
    src = "/tmp/src_" + job["name"] + ".mp4"
    if not os.path.exists(src):
        print("downloading source...")
        req = urllib.request.Request(job["source_url"], headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as r, open(src, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
        print("source bytes:", os.path.getsize(src))
    bottom = job.get("bottom_text", "")
    open("/tmp/bot.txt", "w").write(bottom)
    for c in todo:
        open("/tmp/top.txt", "w").write(c.get("top", ""))
        st, en = float(c["start"]), float(c["end"])
        fo = max(en - st - 0.4, 0)
        draw_top = f"drawtext=fontfile='{FONT}':textfile=/tmp/top.txt:fontsize=64:fontcolor=white:x=(w-text_w)/2:y=330:box=1:boxcolor=black@0.55:boxborderw=24:line_spacing=10:text_align=C,"
        draw_bot = (f"drawtext=fontfile='{FONT}':textfile=/tmp/bot.txt:fontsize=58:fontcolor=0xFFD24A:x=(w-text_w)/2:y=1420:box=1:boxcolor=black@0.55:boxborderw=22:line_spacing=12:text_align=C," if bottom else "")
        fc = (
            "[0:v]fps=30,split=2[a][b];"
            "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:3,eq=brightness=-0.12[bg];"
            "[b]scale=1080:-2:flags=lanczos,eq=contrast=1.06:saturation=1.08[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,"
            + (draw_top if c.get("top") else "") + draw_bot +
            "format=yuv420p[v];"
            f"[0:a]afade=t=in:d=0.25,afade=t=out:st={fo:.2f}:d=0.4,loudnorm=I=-14:TP=-1.5[au]"
        )
        out = os.path.join(outdir, c["out"] + ".mp4")
        cmd = ["ffmpeg", "-loglevel", "error", "-y", "-ss", str(st), "-to", str(en), "-i", src,
               "-filter_complex", fc, "-map", "[v]", "-map", "[au]", "-c:v", "libx264", "-preset", "veryfast",
               "-crf", "21", "-profile:v", "high", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out]
        subprocess.run(cmd, check=True)
        print("rendered", out, os.path.getsize(out))


if __name__ == "__main__":
    jobs = sys.argv[1:] or sorted(glob.glob("jobs/*.json"))
    for j in jobs:
        run_job(j)
