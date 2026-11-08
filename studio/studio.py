#!/usr/bin/env python3
"""我的配音工作台 —— 输入文字，一键生成本人声音的配音（GPT-SoVITS + ASR 内容校验）
启动: python3 studio.py  → http://127.0.0.1:8899
环境变量: GSV_URL(默认 http://127.0.0.1:9880) VOICE_DIR(样本目录) STUDIO_PORT
多语言: 支持 zh/ja/en/ko/yue 输出（页面可选或自动识别）。参考样本为中文录音，
prompt_lang 固定 zh，靠 GPT-SoVITS 跨语种推理保持音色、换目标语言发音。
校验纪律（voice_clone_verification 技能）: 每段合成后 whisper 回转写（按目标语言），
与原文比对，发现参考句泄漏或内容不符 → 自动重试一次，仍不符则标记异常，不当成品交付。
"""
import json, os, re, subprocess, sys, threading, time, urllib.request, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
GSV_URL = os.environ.get("GSV_URL", "http://127.0.0.1:9880")
VOICE_DIR = os.environ.get("VOICE_DIR", os.path.expanduser("~/workspace/digital-human-lab/voice"))
OUT = os.path.join(HERE, "outputs")
os.makedirs(OUT, exist_ok=True)
PORT = int(os.environ.get("STUDIO_PORT", "8899"))
WHISPER_PY = os.environ.get("WHISPER_PY", os.path.expanduser(
    "~/workspace/digital-human-lab/GPT-SoVITS/.venv/bin/python"))
WHISPER_MODEL_DIR = os.environ.get("WHISPER_MODEL_DIR", os.path.join(os.path.expanduser("~"), ".voice-studio-whisper"))

with open(os.path.join(VOICE_DIR, "ref_text.txt"), encoding="utf-8") as f:
    REF_TEXT = f.read().strip()
REF_AUDIO = os.path.join(VOICE_DIR, "ref.wav")
REF_LEAK_TOKENS = ["开心的事", "聊点开心", "欢迎来到我的直播间", "老朋友"]

# 页面语言代码 -> (GPT-SoVITS text_lang, whisper 语言, 显示名)
LANG_MAP = {
    "zh":  ("zh",  "zh", "中文"),
    "ja":  ("ja",  "ja", "日本語"),
    "en":  ("en",  "en", "English"),
    "ko":  ("ko",  "ko", "한국어"),
    "yue": ("yue", "yue", "粤语"),
}

JOBS = {}
LOCK = threading.Lock()

def detect_lang(text):
    if re.search(r"[぀-ヿ]", text):          # 平假名/片假名 -> 日语
        return "ja"
    if re.search(r"[가-힯]", text):          # 谚文 -> 韩语
        return "ko"
    cjk = len(re.findall(r"[一-鿿]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if latin > 0 and latin >= cjk:               # 拉丁字母占主导 -> 英语
        return "en"
    return "zh"

def split_sentences(text, lang="zh"):
    if lang == "en":
        text = re.sub(r"\s+", " ", text.strip())
        parts = re.split(r"(?<=[.!?;])\s+", text)
        out = [p for p in parts if p]
        minlen = 15
    else:
        text = re.sub(r"\s+", "", text.strip())
        parts = re.split(r"(?<=[。！？；….!?;])", text)
        out = [p for p in parts if p]
        minlen = 6
    merged = []
    for p in out:  # 太短的碎片并入上一句，避免合成碎裂
        if merged and len(p) < minlen:
            merged[-1] += p
        else:
            merged.append(p)
    return merged

def gsv_tts(text, speed=1.0, lang="zh"):
    gsv_lang = LANG_MAP[lang][0]
    body = json.dumps({
        "text": text, "text_lang": gsv_lang,
        "ref_audio_path": REF_AUDIO, "prompt_text": REF_TEXT, "prompt_lang": "zh",
        "media_type": "wav", "streaming_mode": False, "speed_factor": speed,
    }).encode("utf-8")
    req = urllib.request.Request(GSV_URL + "/tts", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1200) as r:
        return r.read()

def asr_check(wav_path, expected_text, lang="zh"):
    """whisper 回转写 + 与原文比对。返回 (ok, transcript, issues[], tool_error)
    tool_error 非空 = 校验工具本身没跑起来（缺模块/缺 ffmpeg/模型没下下来），
    这和“内容不对”是两回事：工具坏了不拦成品，带警告交付并写明原因。"""
    asr_lang = LANG_MAP[lang][1]
    code = (
        "import whisper,sys;"
        f"m=whisper.load_model('tiny',download_root={WHISPER_MODEL_DIR!r});"
        f"r=m.transcribe({wav_path!r},language={asr_lang!r});"
        "print(r['text'])"
    )
    r = subprocess.run([WHISPER_PY, "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=900)
    if r.returncode != 0:
        err_lines = [ln.strip() for ln in (r.stderr or "").splitlines() if ln.strip()]
        tool_error = err_lines[-1][:220] if err_lines else f"whisper 退出码 {r.returncode}"
        return False, "", [], tool_error
    transcript = (r.stdout or "").strip().splitlines()[-1] if r.stdout.strip() else ""
    issues = []
    for tok in REF_LEAK_TOKENS:
        if tok in transcript and tok not in expected_text:
            issues.append(f"疑似参考句泄漏：{tok}")
    def norm(s):
        return re.sub(r"[，。！？；、,.!?;\s・「」『』]", "", s)
    exp, got = norm(expected_text), norm(transcript)
    if exp:
        if len(got) < len(exp) * 0.55:
            issues.append("转写明显短于原文，可能有丢句")
    return (len(issues) == 0), transcript, issues, None

def concat_wavs(paths, out_path, gap=0.35):
    lst = os.path.join(OUT, f"list-{uuid.uuid4().hex[:8]}.txt")
    sil = os.path.join(OUT, "gap.wav")
    if not os.path.exists(sil):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "anullsrc=r=32000:cl=mono", "-t", str(gap), sil], check=True)
    with open(lst, "w") as f:
        for i, p in enumerate(paths):
            f.write(f"file '{p}'\n")
            if i < len(paths) - 1:
                f.write(f"file '{sil}'\n")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                    "-i", lst, "-c", "copy", out_path], check=True)
    os.remove(lst)

def run_job(job_id, text, speed, lang="zh"):
    job = JOBS[job_id]
    job["lang"] = lang
    try:
        sents = split_sentences(text, lang)
        job["total"] = len(sents)
        wavs = []
        check_broken = None
        for i, s in enumerate(sents):
            job["step"] = f"正在合成第 {i+1}/{len(sents)} 句（{LANG_MAP[lang][2]}）"
            seg = os.path.join(OUT, f"{job_id}-{i}.wav")
            for attempt in range(2):
                data = gsv_tts(s, speed, lang)
                with open(seg, "wb") as f:
                    f.write(data)
                ok, transcript, issues, tool_error = asr_check(seg, s, lang)
                if tool_error:
                    check_broken = tool_error
                    break  # 校验工具坏了，重合成也没用，带警告继续
                if ok:
                    break
                job.setdefault("warnings", []).append(f"第{i+1}句校验异常({'; '.join(issues)})，已自动重试")
            else:
                job["check"] = "failed"
                job["status"] = "error"
                job["error"] = f"第 {i+1} 句内容校验未通过：{issues}。转写：{transcript}"
                return
            if check_broken:
                job.setdefault("warnings", []).append(
                    f"内容校验工具异常（{check_broken}），成品未经内容校验，请试听确认")
            wavs.append(seg)
        job["step"] = "正在拼接成品"
        final_wav = os.path.join(OUT, f"{job_id}.wav")
        concat_wavs(wavs, final_wav)
        final_mp3 = os.path.join(OUT, f"{job_id}.mp3")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", final_wav,
                        "-codec:a", "libmp3lame", "-q:a", "4", final_mp3], check=True)
        ok, transcript, issues, tool_error = asr_check(final_mp3, text, lang)
        job["transcript"] = transcript
        job["url"] = f"/outputs/{job_id}.mp3"
        if tool_error or check_broken:
            job["check"] = "warning"
            job.setdefault("warnings", []).append("成品未做内容校验（校验工具异常），请试听确认")
        else:
            job["check"] = "passed" if ok else "warning"
            if not ok:
                job["warnings"] = job.get("warnings", []) + issues
        job["status"] = "done"
        job["step"] = "完成"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)[:400]

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
            pass  # 客户端先断开（探测超时/刷新页面）是正常现象，不刷 traceback

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            with open(os.path.join(HERE, "index.html"), encoding="utf-8") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif self.path.startswith("/outputs/"):
            fp = os.path.join(OUT, os.path.basename(self.path))
            if os.path.exists(fp):
                with open(fp, "rb") as f:
                    self._send(200, f.read(), "audio/mpeg")
            else:
                self._send(404, "{}")
        elif self.path.startswith("/api/job/"):
            job = JOBS.get(self.path.split("/")[-1])
            self._send(200 if job else 404, json.dumps(job or {"error": "no job"}, ensure_ascii=False))
        elif self.path == "/api/status":
            up = False
            try:
                urllib.request.urlopen(GSV_URL, timeout=1); up = True
            except Exception as e:
                up = hasattr(e, "code")
            self._send(200, json.dumps({"engine_up": up, "ref_ready": os.path.exists(REF_AUDIO)}))
        else:
            self._send(404, "{}")

    def do_POST(self):
        if self.path != "/api/generate":
            return self._send(404, "{}")
        n = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(n) or b"{}")
        text = (data.get("text") or "").strip()
        if not text:
            return self._send(400, json.dumps({"error": "先输入文字"}, ensure_ascii=False))
        if len(text) > 2000:
            return self._send(400, json.dumps({"error": "一次最多 2000 字"}, ensure_ascii=False))
        speed = float(data.get("speed") or 1.0)
        lang = (data.get("lang") or "auto").strip()
        if lang not in LANG_MAP:
            lang = detect_lang(text)
        job_id = time.strftime("%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        with LOCK:
            JOBS[job_id] = {"status": "running", "step": "排队中", "total": 0, "lang": lang}
        threading.Thread(target=run_job, args=(job_id, text, speed, lang), daemon=True).start()
        self._send(200, json.dumps({"job_id": job_id, "lang": lang}))

class QuietServer(ThreadingHTTPServer):
    daemon_threads = True
    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)):
            return  # 客户端中止连接不算服务端错误，保持窗口干净
        super().handle_error(request, client_address)

if __name__ == "__main__":
    print(f"配音工作台: http://127.0.0.1:{PORT}  引擎: {GSV_URL}  样本: {VOICE_DIR}")
    QuietServer(("127.0.0.1", PORT), H).serve_forever()
