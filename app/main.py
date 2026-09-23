"""FastAPI backend. Everything runs locally; no outbound network calls."""
import atexit
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .engine.options import Options
from .engine.pipeline import PdfSession

STATIC = Path(__file__).parent / "static"
WORK = Path(tempfile.mkdtemp(prefix="pdf2epub_"))
atexit.register(shutil.rmtree, WORK, ignore_errors=True)

app = FastAPI(title="PDF to EPUB")
SESSIONS: dict[str, PdfSession] = {}
MAX_UPLOAD = 300 * 2**20       # bytes
MAX_SESSIONS = 8               # oldest uploads are released beyond this


def _session(doc_id: str) -> PdfSession:
    s = SESSIONS.get(doc_id)
    if not s:
        raise HTTPException(404, "Unknown document; upload it again.")
    return s


def _evict():
    """Release the oldest documents (open file handle, caches, temp files) beyond MAX_SESSIONS."""
    for old_id in list(SESSIONS):
        if len(SESSIONS) <= MAX_SESSIONS:
            break
        old = SESSIONS[old_id]
        if old.job["state"] == "running":
            continue
        del SESSIONS[old_id]
        try:
            with old.lock:
                old.doc.close()
        except Exception:
            pass
        for suffix in (".pdf", ".epub"):
            (WORK / f"{old_id}{suffix}").unlink(missing_ok=True)


def _deliver(s: PdfSession, epub: Path) -> Path:
    """Copy the finished EPUB to the user's Downloads folder (never overwriting) and return its path."""
    folder = Path(os.environ.get("PDF2EPUB_OUT_DIR") or Path.home() / "Downloads")
    if not folder.is_dir():
        folder = Path.home()
    name = re.sub(r"[^\w\- ]+", "", s.title).strip()[:120] or "book"
    dest, k = folder / f"{name}.epub", 1
    while dest.exists():
        k += 1
        dest = folder / f"{name} ({k}).epub"
    shutil.copy2(epub, dest)
    return dest


def _reveal(path: Path) -> None:
    """Open the file's folder in the OS file manager with the file selected where supported."""
    if os.environ.get("PDF2EPUB_NO_REVEAL"):
        return
    if sys.platform == "darwin":
        cmd = ["open", "-R", str(path)]
    elif os.name == "nt":
        cmd = ["explorer", f"/select,{path}"]
    else:
        cmd = ["xdg-open", str(path.parent)]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _opts(request: Request) -> Options:
    return Options.from_dict(dict(request.query_params))


@app.post("/api/upload")
def upload(file: UploadFile = File(...)):
    data = file.file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, f"PDF is larger than {MAX_UPLOAD // 2**20} MB.")
    if b"%PDF" not in data[:1024]:
        raise HTTPException(400, "That does not look like a PDF file.")
    doc_id = uuid.uuid4().hex[:12]
    path = WORK / f"{doc_id}.pdf"
    path.write_bytes(data)
    try:
        session = PdfSession(path, name=file.filename or "")
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(400, f"Could not read PDF: {exc}")
    SESSIONS[doc_id] = session
    _evict()
    return {"doc_id": doc_id, **session.info()}


@app.get("/api/{doc_id}/page/{n}/image")
def page_image(doc_id: str, n: int):
    s = _session(doc_id)
    if not 0 <= n < len(s.pages):
        raise HTTPException(404, "No such page")
    return Response(s.page_png(n), media_type="image/png", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/{doc_id}/img/{xref}")
def embedded_image(doc_id: str, xref: int):
    got = _session(doc_id).image_bytes(xref)
    if not got:
        raise HTTPException(404, "Image unreadable")
    data, ext = got
    return Response(data, media_type={"jpg": "image/jpeg", "png": "image/png", "gif": "image/gif"}[ext],
                    headers={"Cache-Control": "max-age=3600"})


@app.get("/api/{doc_id}/fig/{n}/{rot}")
def figure_page(doc_id: str, n: int, rot: int, clip: str = ""):
    s = _session(doc_id)
    if not 0 <= n < len(s.pages) or rot not in (0, 90, 180, 270):
        raise HTTPException(404, "No such page")
    box = None
    if clip:
        try:
            box = tuple(float(v) for v in clip.split(","))
            if len(box) != 4 or not all(v == v and abs(v) < 1e5 for v in box):
                raise ValueError
        except ValueError:
            raise HTTPException(400, "clip must be x0,y0,x1,y1")
    return Response(s.render_figure(n, rot, box), media_type="image/png", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/{doc_id}/page/{n}/preview")
def page_preview(doc_id: str, n: int, request: Request):
    s = _session(doc_id)
    url = lambda im: (f"/api/{doc_id}/img/{im.xref}" if im.page is None
                      else f"/api/{doc_id}/fig/{im.page}/{im.rot}" + (
                          "?clip=" + ",".join(f"{v:.1f}" for v in im.clip) if im.clip else ""))
    return s.preview(n, _opts(request), url)


@app.post("/api/{doc_id}/convert")
async def convert(doc_id: str, request: Request):
    s = _session(doc_id)
    if s.job["state"] == "running":
        raise HTTPException(409, "Conversion already running")
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    opts = Options.from_dict(body)
    out = WORK / f"{doc_id}.epub"
    s.job.update(state="running", progress=0.0, log=["Queued."], file=None, saved=None)

    def work():
        try:
            s.convert(opts, out)
        except Exception:
            return
        try:
            dest = _deliver(s, out)
            s.job["saved"] = str(dest)
            s.job["log"].append(f"Saved to {dest}")
            _reveal(dest)
        except Exception as exc:                      # conversion succeeded; delivery is best-effort
            s.job["log"].append(f"Could not save to Downloads ({exc}); use the Download button instead.")

    threading.Thread(target=work, daemon=True).start()
    return {"ok": True}


@app.get("/api/{doc_id}/status")
def status(doc_id: str):
    j = _session(doc_id).job
    return JSONResponse({"state": j["state"], "progress": j["progress"], "log": list(j["log"]),
                         "saved": j.get("saved")})


@app.post("/api/{doc_id}/reveal")
def reveal(doc_id: str):
    saved = _session(doc_id).job.get("saved")
    if not saved or not Path(saved).exists():
        raise HTTPException(404, "Nothing saved yet")
    _reveal(Path(saved))
    return {"ok": True}


@app.get("/api/{doc_id}/download")
def download(doc_id: str):
    s = _session(doc_id)
    if s.job["state"] != "done" or not s.job["file"]:
        raise HTTPException(409, "No finished conversion yet")
    name = re.sub(r"[^\w\- ]+", "", s.title).strip()[:120] or "book"
    return FileResponse(s.job["file"], media_type="application/epub+zip", filename=f"{name}.epub")


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
