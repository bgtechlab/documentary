#!/usr/bin/env python3
"""
documentary_bot.py - Hindi Documentary Video Bot (5-10 min, faceless)

Pipeline:
  Topic -> Gemini script (scenes) -> edge-tts Hindi female voice
  -> Pexels images -> ffmpeg (Ken Burns + subtitles + music)
  -> Pillow thumbnail -> YouTube upload (default: private)

ENV (GitHub Secrets):
  GEMINI_API_KEY, PEXELS_API_KEY
  YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN   (optional - upload skip ho jayega agar nahi hain)
ENV (workflow inputs):
  TOPIC ("auto" = Gemini khud topic chunega), MINUTES (5-10), PRIVACY (private/unlisted/public)
"""
import os, sys, json, re, math, glob, random, subprocess, time, shutil
import requests

GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
PEXELS_KEY = os.getenv("PEXELS_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL") or "gemini-3.6-flash"  # pehle yahi, fail ho to auto-detect
TOPIC = (os.getenv("TOPIC") or "auto").strip()
MINUTES = max(5, min(10, int(os.getenv("MINUTES") or 7)))
PRIVACY = os.getenv("PRIVACY", "private")
VOICE = os.getenv("VOICE", "hi-IN-SwaraNeural")  # female Hindi voice
W, H, FPS = 1280, 720, 25
WORK = "work"
OUT = "output"
IMG_SECONDS = 6  # ek image kitni der dikhegi


def log(*a):
    print("[bot]", *a, flush=True)


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        log("CMD FAILED:", " ".join(cmd)[:300])
        log(r.stderr[-1500:])
        raise RuntimeError("command failed")
    return r.stdout


# ------------------------------------------------------------------ Gemini
_MODELS = None
_BAD = ("lite", "image", "tts", "live", "audio", "embedding", "robotics", "computer", "thinking", "exp", "vision")


def candidate_models():
    """Account me jo Gemini flash models available hain unki list (naye pehle)."""
    global _MODELS
    if _MODELS is not None:
        return _MODELS
    names = []
    if GEMINI_MODEL:
        names.append(GEMINI_MODEL)
    try:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                         params={"key": GEMINI_KEY, "pageSize": 200}, timeout=30)
        r.raise_for_status()
        stable, preview = [], []
        for m in r.json().get("models", []):
            n = m["name"].split("/")[-1]
            if "generateContent" not in m.get("supportedGenerationMethods", []):
                continue
            if "flash" not in n or any(b in n for b in _BAD):
                continue
            (preview if "preview" in n else stable).append(n)
        names += sorted(stable, reverse=True) + sorted(preview, reverse=True)
    except Exception as e:
        log("Model list fetch failed:", e)
    names += ["gemini-flash-latest", "gemini-2.5-flash"]
    _MODELS = list(dict.fromkeys(names))
    log("Gemini models to try:", _MODELS[:6])
    return _MODELS


def gemini_json(prompt, retries=2):
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.8},
    }
    last = None
    for model in candidate_models():
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
        for i in range(retries):
            try:
                r = requests.post(url, json=body, timeout=180)
                if r.status_code in (400, 404):
                    last = f"{model}: HTTP {r.status_code} {r.text[:200]}"
                    log("model skip:", last)
                    break  # agla model try karo
                r.raise_for_status()
                txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                txt = re.sub(r"^```(?:json)?|```$", "", txt.strip()).strip()
                data = json.loads(txt)
                global _MODELS
                _MODELS = [model]  # chalne wala model yaad rakho
                return data
            except Exception as e:
                last = f"{model}: {e}"
                log("Gemini retry", i + 1, last)
                time.sleep(5 * (i + 1))
    raise RuntimeError(f"Gemini failed: {last}")


def pick_topic():
    if TOPIC.lower() != "auto":
        return TOPIC
    p = (
        "Suggest ONE fascinating topic for a 5-10 minute Hindi documentary for an Indian YouTube audience. "
        "Choose from history, science, space, technology, mysteries, culture, geography or biography. "
        'Return JSON: {"topic": "<topic in Hindi (Devanagari)>"}'
    )
    return gemini_json(p)["topic"]


def make_script(topic):
    words_total = MINUTES * 130  # Hindi narration ~130 words/min
    n_scenes = max(10, MINUTES * 3)
    per_scene = words_total // n_scenes
    p = f"""You are a documentary script writer for a Hindi YouTube channel.
Topic: {topic}
Target length: about {MINUTES} minutes of narration = about {words_total} Hindi words in total.

Write a engaging documentary with this structure: strong hook (first scene), short intro, main chapters, surprising facts, conclusion with a call to subscribe.
Rules:
- Narration language: Hindi in Devanagari script ONLY. Simple spoken Hindi, short sentences, no English words unless it is a proper noun. No emojis, no markdown, no stage directions.
- Only well-known, verifiable facts. If unsure about a number or date, avoid it.
- Exactly {n_scenes} scenes, each narration about {per_scene} words.
- For every scene give 3 'keywords': English stock-photo search phrases (2-4 words, concrete and visual, e.g. "ancient temple india", "rocket launch night"). Do not use people's names.

Return ONLY JSON:
{{
  "title": "catchy Hindi title (Devanagari), max 90 characters",
  "thumb_text": "3-5 powerful Hindi words for thumbnail",
  "description": "Hindi description 3-4 lines",
  "tags": ["tag1", "tag2", "... 10 tags, mix Hindi and English"],
  "scenes": [ {{"narration": "...", "keywords": ["...", "...", "..."]}} ]
}}"""
    data = gemini_json(p)
    scenes = [s for s in data.get("scenes", []) if s.get("narration")]
    if len(scenes) < 5:
        raise RuntimeError("Script too short")
    data["scenes"] = scenes
    return data


# ------------------------------------------------------------------ Voice
def audio_duration(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", path])
    return float(out.strip())


def make_voice(text, idx):
    txt_path = f"{WORK}/s{idx:02d}.txt"
    mp3 = f"{WORK}/s{idx:02d}.mp3"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(text)
    for i in range(3):
        try:
            run(["edge-tts", "--voice", VOICE, "--rate=-3%", "--file", txt_path, "--write-media", mp3])
            if os.path.getsize(mp3) > 1000:
                return mp3
        except Exception as e:
            log("edge-tts retry", i + 1, e)
            time.sleep(3)
    raise RuntimeError("TTS failed for scene %d" % idx)


# ------------------------------------------------------------------ Images
USED = set()


def pexels_images(keywords, need, tag):
    """need images download karta hai, files ki list return karta hai."""
    paths = []
    if not PEXELS_KEY:
        return paths
    for kw in keywords:
        if len(paths) >= need:
            break
        try:
            r = requests.get(
                "https://api.pexels.com/v1/search",
                params={"query": kw, "per_page": 15, "orientation": "landscape"},
                headers={"Authorization": PEXELS_KEY}, timeout=30)
            r.raise_for_status()
            photos = r.json().get("photos", [])
            random.shuffle(photos)
            for ph in photos:
                if ph["id"] in USED:
                    continue
                url = ph["src"].get("large2x") or ph["src"]["large"]
                img = requests.get(url, timeout=60)
                if img.status_code != 200:
                    continue
                p = f"{WORK}/img_{tag}_{len(paths)}.jpg"
                with open(p, "wb") as f:
                    f.write(img.content)
                USED.add(ph["id"])
                paths.append(p)
                if len(paths) >= need:
                    break
        except Exception as e:
            log("pexels error", kw, e)
    return paths


def fallback_image(tag, n):
    from PIL import Image, ImageDraw
    p = f"{WORK}/img_{tag}_fb{n}.jpg"
    img = Image.new("RGB", (1920, 1080))
    d = ImageDraw.Draw(img)
    c1 = (random.randint(10, 60), random.randint(10, 60), random.randint(40, 120))
    c2 = (random.randint(0, 30), random.randint(0, 30), random.randint(0, 50))
    for y in range(1080):
        t = y / 1080
        d.line([(0, y), (1920, y)], fill=tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(3)))
    img.save(p, quality=90)
    return p


# ------------------------------------------------------------------ Video
def ken_burns_clip(img, dur, out, zoom_in=True):
    frames = max(1, int(round(dur * FPS)))
    if zoom_in:
        z = "min(1+0.0012*on,1.25)"
    else:
        z = "max(1.25-0.0012*on,1.0)"
    vf = (
        "scale=2560:1440:force_original_aspect_ratio=increase,crop=2560:1440,"
        f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS},"
        "format=yuv420p"
    )
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", img, "-vf", vf,
         "-frames:v", str(frames), "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", out])


def build_scene(idx, scene, mp3, dur):
    total = dur + 0.4
    n = max(1, math.ceil(total / IMG_SECONDS))
    imgs = pexels_images(scene.get("keywords", []), n, f"{idx:02d}")
    while len(imgs) < n:
        imgs.append(imgs[-1] if imgs else fallback_image(f"{idx:02d}", len(imgs)))
    seg = total / n
    clips = []
    for k, im in enumerate(imgs):
        c = f"{WORK}/s{idx:02d}_c{k}.mp4"
        ken_burns_clip(im, seg, c, zoom_in=((idx + k) % 2 == 0))
        clips.append(c)
    lst = f"{WORK}/s{idx:02d}_list.txt"
    with open(lst, "w") as f:
        for c in clips:
            f.write(f"file '{os.path.abspath(c)}'\n")
    vid = f"{WORK}/s{idx:02d}_v.mp4"
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", vid])
    final = f"{WORK}/scene_{idx:02d}.mp4"
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", vid, "-i", mp3,
         "-af", "apad", "-t", f"{total:.3f}", "-c:v", "copy",
         "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "160k", final])
    return final, total


def srt_time(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(scenes, durs, path):
    lines, n, offset = [], 1, 0.0
    for sc, total in zip(scenes, durs):
        words = sc["narration"].split()
        chunks = [" ".join(words[i:i + 7]) for i in range(0, len(words), 7)]
        speech = max(0.5, total - 0.4)
        weights = [max(1, len(c)) for c in chunks]
        tw = sum(weights)
        t = offset
        for c, w in zip(chunks, weights):
            d = speech * w / tw
            lines += [str(n), f"{srt_time(t)} --> {srt_time(t + d)}", c, ""]
            n += 1
            t += d
        offset += total
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def final_render(scene_files, srt_path, out_path):
    lst = f"{WORK}/all.txt"
    with open(lst, "w") as f:
        for s in scene_files:
            f.write(f"file '{os.path.abspath(s)}'\n")
    joined = f"{WORK}/joined.mp4"
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", joined])

    style = ("FontName=Noto Sans Devanagari,FontSize=13,Bold=1,PrimaryColour=&H00FFFFFF,"
             "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,MarginV=28")
    sub = f"subtitles={os.path.basename(srt_path)}:force_style='{style}'"
    music = sorted(glob.glob("assets/music/*.mp3"))
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", os.path.abspath(joined)]
    if music:
        cmd += ["-stream_loop", "-1", "-i", os.path.abspath(random.choice(music)),
                "-filter_complex",
                f"[0:v]{sub}[v];[1:a]volume=0.07[m];[0:a][m]amix=inputs=2:duration=first:dropout_transition=0[a]",
                "-map", "[v]", "-map", "[a]"]
    else:
        cmd += ["-vf", sub, "-map", "0:v", "-map", "0:a"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", os.path.abspath(out_path)]
    run(cmd, cwd=WORK)  # cwd=WORK taaki subtitles path simple rahe


# ------------------------------------------------------------------ Thumbnail
def find_font():
    for pat in ["/usr/share/fonts/**/NotoSansDevanagari-Bold.ttf",
                "/usr/share/fonts/**/NotoSansDevanagari*Bold*.ttf",
                "/usr/share/fonts/**/NotoSansDevanagari*.ttf"]:
        f = glob.glob(pat, recursive=True)
        if f:
            return f[0]
    return None


def make_thumbnail(text, bg_img, out_path):
    from PIL import Image, ImageDraw, ImageFont, ImageEnhance
    img = Image.open(bg_img).convert("RGB")
    img = img.resize((1280, 720)) if img.size != (1280, 720) else img
    img = ImageEnhance.Brightness(img).enhance(0.55)
    img = ImageEnhance.Contrast(img).enhance(1.2)
    d = ImageDraw.Draw(img)
    font_path = find_font()
    size = 120
    font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()

    def wrap(t, fnt, maxw):
        lines, cur = [], ""
        for w in t.split():
            test = (cur + " " + w).strip()
            if d.textlength(test, font=fnt) <= maxw:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines

    lines = wrap(text, font, 1120)
    while len(lines) > 3 and size > 70 and font_path:
        size -= 10
        font = ImageFont.truetype(font_path, size)
        lines = wrap(text, font, 1120)
    lh = size * 1.35
    y = (720 - lh * len(lines)) / 2
    for ln in lines:
        w = d.textlength(ln, font=font)
        x = (1280 - w) / 2
        d.text((x, y), ln, font=font, fill=(255, 215, 0),
               stroke_width=8, stroke_fill=(0, 0, 0))
        y += lh
    img.save(out_path, quality=92)


# ------------------------------------------------------------------ YouTube
def upload_youtube(video, thumb, meta):
    cid, sec, rt = os.getenv("YT_CLIENT_ID"), os.getenv("YT_CLIENT_SECRET"), os.getenv("YT_REFRESH_TOKEN")
    if not (cid and sec and rt):
        log("YouTube secrets nahi mile - upload skip.")
        return None
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds = Credentials(None, refresh_token=rt, client_id=cid, client_secret=sec,
                        token_uri="https://oauth2.googleapis.com/token",
                        scopes=["https://www.googleapis.com/auth/youtube.upload"])
    yt = build("youtube", "v3", credentials=creds)
    body = {
        "snippet": {
            "title": meta["title"][:100],
            "description": meta["description"][:4900],
            "tags": meta.get("tags", [])[:15],
            "categoryId": "27",  # Education
            "defaultLanguage": "hi",
            "defaultAudioLanguage": "hi",
        },
        "status": {"privacyStatus": PRIVACY, "selfDeclaredMadeForKids": False,
                   "containsSyntheticMedia": True},
    }
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(video, chunksize=8 * 1024 * 1024, resumable=True))
    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            log("upload %d%%" % int(status.progress() * 100))
    vid = resp["id"]
    log("Uploaded: https://youtu.be/" + vid)
    try:
        yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(thumb)).execute()
    except Exception as e:
        log("Thumbnail set failed (channel verified hai?):", e)
    return vid


# ------------------------------------------------------------------ main
def main():
    if not GEMINI_KEY:
        sys.exit("GEMINI_API_KEY missing")
    if not PEXELS_KEY:
        log("WARNING: PEXELS_API_KEY nahi hai - sirf plain background use honge.")
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK)
    os.makedirs(OUT, exist_ok=True)

    topic = pick_topic()
    log("Topic:", topic)
    data = make_script(topic)
    scenes = data["scenes"]
    log("Title:", data["title"], "| scenes:", len(scenes))
    with open(f"{OUT}/script.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    scene_files, durs = [], []
    for i, sc in enumerate(scenes):
        mp3 = make_voice(sc["narration"], i)
        dur = audio_duration(mp3)
        sf, total = build_scene(i, sc, mp3, dur)
        scene_files.append(sf)
        durs.append(total)
        log(f"scene {i + 1}/{len(scenes)} done ({total:.1f}s)")

    log("Total length: %.1f min" % (sum(durs) / 60))
    srt = f"{WORK}/subs.srt"
    write_srt(scenes, durs, srt)

    video = f"{OUT}/documentary.mp4"
    final_render(scene_files, srt, video)

    bg = next(iter(sorted(glob.glob(f"{WORK}/img_*.jpg"))), None)
    thumb = f"{OUT}/thumbnail.jpg"
    if bg:
        make_thumbnail(data.get("thumb_text") or data["title"], bg, thumb)

    data["description"] = (data["description"] +
                           "\n\nFootage: Pexels (free license). Voice: AI generated.\n"
                           "इस वीडियो में AI वॉइस और स्टॉक फुटेज का उपयोग हुआ है।")
    upload_youtube(video, thumb, data)
    log("DONE")


if __name__ == "__main__":
    main()
