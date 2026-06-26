from fastapi import FastAPI, APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import io
import logging
import uuid
import zipfile
import tempfile
from pathlib import Path
from typing import List, Optional
import fitz  # PyMuPDF
from PIL import Image
import qrcode

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

app = FastAPI(title="Toolverse API")
api_router = APIRouter(prefix="/api")


@api_router.get("/")
async def root():
    return {"message": "Toolverse API is running", "version": "1.0"}


def _streaming(data: bytes, filename: str, media_type: str = "application/pdf"):
    return StreamingResponse(
        io.BytesIO(data),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


# ---------- PDF TOOLS ----------

@api_router.post("/pdf/merge")
async def merge_pdf(files: List[UploadFile] = File(...)):
    if len(files) < 2:
        raise HTTPException(400, "At least 2 PDFs required")
    merged = fitz.open()
    try:
        for f in files:
            data = await f.read()
            doc = fitz.open(stream=data, filetype="pdf")
            merged.insert_pdf(doc)
            doc.close()
        out = merged.tobytes()
        merged.close()
        return _streaming(out, "toolverse-merged.pdf")
    except Exception as e:
        raise HTTPException(500, f"Merge failed: {str(e)}")


@api_router.post("/pdf/split")
async def split_pdf(file: UploadFile = File(...), ranges: Optional[str] = Form(None)):
    """Split PDF. ranges like '1-3,5,7-9'. If empty, every page as separate file."""
    data = await file.read()
    src = fitz.open(stream=data, filetype="pdf")
    n = src.page_count
    parts = []
    try:
        if ranges:
            for chunk in ranges.split(","):
                chunk = chunk.strip()
                if "-" in chunk:
                    a, b = chunk.split("-")
                    a, b = max(1, int(a)) - 1, min(n, int(b)) - 1
                else:
                    a = b = int(chunk) - 1
                if a > b or a < 0 or b >= n:
                    continue
                parts.append((a, b))
        else:
            parts = [(i, i) for i in range(n)]

        zbuf = io.BytesIO()
        with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as zf:
            for idx, (a, b) in enumerate(parts, 1):
                new = fitz.open()
                new.insert_pdf(src, from_page=a, to_page=b)
                zf.writestr(f"split-{idx}_p{a+1}-{b+1}.pdf", new.tobytes())
                new.close()
        zbuf.seek(0)
        return _streaming(zbuf.read(), "toolverse-split.zip", "application/zip")
    finally:
        src.close()


@api_router.post("/pdf/compress")
async def compress_pdf(file: UploadFile = File(...), level: str = Form("medium")):
    data = await file.read()
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        # Image downsampling by level
        dpi_map = {"low": 150, "medium": 100, "high": 72}
        target_dpi = dpi_map.get(level, 100)
        quality_map = {"low": 80, "medium": 60, "high": 40}
        q = quality_map.get(level, 60)

        for page in doc:
            for img in page.get_images(full=True):
                xref = img[0]
                try:
                    pix = fitz.Pixmap(doc, xref)
                    if pix.n - pix.alpha >= 4:
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    pil = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    # resize down
                    scale = min(1.0, target_dpi / 150)
                    new_size = (max(1, int(pil.width * scale)), max(1, int(pil.height * scale)))
                    pil = pil.resize(new_size, Image.LANCZOS)
                    buf = io.BytesIO()
                    pil.save(buf, format="JPEG", quality=q, optimize=True)
                    doc.update_stream(xref, buf.getvalue())
                except Exception:
                    continue
        out = doc.tobytes(garbage=4, deflate=True, clean=True)
        doc.close()
        return _streaming(out, "toolverse-compressed.pdf")
    except Exception as e:
        doc.close()
        raise HTTPException(500, f"Compress failed: {str(e)}")


@api_router.post("/pdf/rotate")
async def rotate_pdf(file: UploadFile = File(...), angle: int = Form(90), pages: Optional[str] = Form(None)):
    data = await file.read()
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        target_pages = []
        if pages:
            for ch in pages.split(","):
                ch = ch.strip()
                if "-" in ch:
                    a, b = ch.split("-")
                    target_pages.extend(range(int(a) - 1, int(b)))
                else:
                    target_pages.append(int(ch) - 1)
        else:
            target_pages = list(range(doc.page_count))
        for i in target_pages:
            if 0 <= i < doc.page_count:
                doc[i].set_rotation((doc[i].rotation + angle) % 360)
        out = doc.tobytes()
        doc.close()
        return _streaming(out, "toolverse-rotated.pdf")
    except Exception as e:
        doc.close()
        raise HTTPException(500, str(e))


@api_router.post("/pdf/to-images")
async def pdf_to_images(file: UploadFile = File(...), fmt: str = Form("jpg"), dpi: int = Form(150)):
    data = await file.read()
    doc = fitz.open(stream=data, filetype="pdf")
    fmt = fmt.lower()
    if fmt not in ("jpg", "jpeg", "png"):
        fmt = "jpg"
    pil_fmt = "JPEG" if fmt in ("jpg", "jpeg") else "PNG"
    zbuf = io.BytesIO()
    try:
        with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as zf:
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            for i, page in enumerate(doc, 1):
                pix = page.get_pixmap(matrix=mat, alpha=False)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                bio = io.BytesIO()
                img.save(bio, format=pil_fmt, quality=92 if pil_fmt == "JPEG" else None)
                zf.writestr(f"page-{i}.{fmt}", bio.getvalue())
        doc.close()
        zbuf.seek(0)
        return _streaming(zbuf.read(), f"toolverse-images.zip", "application/zip")
    except Exception as e:
        doc.close()
        raise HTTPException(500, str(e))


@api_router.post("/pdf/from-images")
async def images_to_pdf(files: List[UploadFile] = File(...)):
    if not files:
        raise HTTPException(400, "No images provided")
    images = []
    try:
        for f in files:
            data = await f.read()
            img = Image.open(io.BytesIO(data)).convert("RGB")
            images.append(img)
        out = io.BytesIO()
        first, rest = images[0], images[1:]
        first.save(out, format="PDF", save_all=True, append_images=rest)
        return _streaming(out.getvalue(), "toolverse-images.pdf")
    except Exception as e:
        raise HTTPException(500, str(e))


@api_router.post("/pdf/watermark")
async def watermark_pdf(
    file: UploadFile = File(...),
    text: str = Form(...),
    opacity: float = Form(0.3),
    angle: int = Form(0),  # PyMuPDF rotate only accepts 0/90/180/270
    color: str = Form("#888888")
):
    data = await file.read()
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        c = color.lstrip("#")
        rgb = tuple(int(c[i:i+2], 16) / 255 for i in (0, 2, 4)) if len(c) == 6 else (0.5, 0.5, 0.5)
        # Snap to nearest valid rotation
        valid = [0, 90, 180, 270]
        angle = min(valid, key=lambda v: abs(v - (angle % 360)))
        for page in doc:
            rect = page.rect
            fs = max(40, int(min(rect.width, rect.height) / 10))
            tw = fitz.get_text_length(text, fontname="helv", fontsize=fs)
            cx, cy = rect.width / 2, rect.height / 2
            page.insert_text(
                fitz.Point(cx - tw / 2, cy),
                text,
                fontsize=fs,
                fontname="helv",
                color=rgb,
                fill_opacity=opacity,
                rotate=angle,
            )
        out = doc.tobytes()
        doc.close()
        return _streaming(out, "toolverse-watermarked.pdf")
    except Exception as e:
        doc.close()
        raise HTTPException(500, str(e))


@api_router.post("/pdf/page-numbers")
async def page_numbers(file: UploadFile = File(...), position: str = Form("bottom-center")):
    data = await file.read()
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        n = doc.page_count
        for i, page in enumerate(doc, 1):
            label = f"{i} / {n}"
            rect = page.rect
            fs = 11
            tw = fitz.get_text_length(label, fontname="helv", fontsize=fs)
            margin = 28
            if "top" in position:
                y = margin
            else:
                y = rect.height - margin
            if "left" in position:
                x = margin
            elif "right" in position:
                x = rect.width - margin - tw
            else:
                x = (rect.width - tw) / 2
            page.insert_text((x, y), label, fontsize=fs, fontname="helv", color=(0.2, 0.2, 0.2))
        out = doc.tobytes()
        doc.close()
        return _streaming(out, "toolverse-numbered.pdf")
    except Exception as e:
        doc.close()
        raise HTTPException(500, str(e))


@api_router.post("/pdf/protect")
async def protect_pdf(file: UploadFile = File(...), password: str = Form(...)):
    data = await file.read()
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        perm = int(
            fitz.PDF_PERM_ACCESSIBILITY
            | fitz.PDF_PERM_PRINT
            | fitz.PDF_PERM_COPY
            | fitz.PDF_PERM_ANNOTATE
        )
        out = doc.tobytes(
            encryption=fitz.PDF_ENCRYPT_AES_256,
            owner_pw=password,
            user_pw=password,
            permissions=perm,
        )
        doc.close()
        return _streaming(out, "toolverse-protected.pdf")
    except Exception as e:
        doc.close()
        raise HTTPException(500, str(e))


@api_router.post("/pdf/unlock")
async def unlock_pdf(file: UploadFile = File(...), password: str = Form(...)):
    data = await file.read()
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        if doc.needs_pass:
            if not doc.authenticate(password):
                doc.close()
                raise HTTPException(400, "Wrong password")
        out = doc.tobytes()
        doc.close()
        return _streaming(out, "toolverse-unlocked.pdf")
    except HTTPException:
        raise
    except Exception as e:
        doc.close()
        raise HTTPException(500, str(e))


@api_router.post("/pdf/organize")
async def organize_pdf(file: UploadFile = File(...), order: str = Form(...)):
    """order: comma-separated 1-based page indices, e.g. '3,1,2,4'"""
    data = await file.read()
    src = fitz.open(stream=data, filetype="pdf")
    try:
        new = fitz.open()
        indices = [int(x.strip()) - 1 for x in order.split(",") if x.strip()]
        for i in indices:
            if 0 <= i < src.page_count:
                new.insert_pdf(src, from_page=i, to_page=i)
        out = new.tobytes()
        new.close()
        src.close()
        return _streaming(out, "toolverse-organized.pdf")
    except Exception as e:
        src.close()
        raise HTTPException(500, str(e))


@api_router.post("/pdf/info")
async def pdf_info(file: UploadFile = File(...)):
    data = await file.read()
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        meta = doc.metadata or {}
        pages = []
        for i, p in enumerate(doc, 1):
            pages.append({"page": i, "width": p.rect.width, "height": p.rect.height})
        info = {
            "pageCount": doc.page_count,
            "title": meta.get("title"),
            "author": meta.get("author"),
            "creator": meta.get("creator"),
            "encrypted": doc.needs_pass,
            "pages": pages[:50],
            "sizeKB": round(len(data) / 1024, 2),
        }
        doc.close()
        return info
    except Exception as e:
        doc.close()
        raise HTTPException(500, str(e))


@api_router.post("/pdf/preview")
async def pdf_preview(file: UploadFile = File(...), page: int = Form(1), dpi: int = Form(100)):
    """Returns PNG preview of a single page."""
    data = await file.read()
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        idx = max(0, min(page - 1, doc.page_count - 1))
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = doc[idx].get_pixmap(matrix=mat, alpha=False)
        img_bytes = pix.tobytes("png")
        doc.close()
        return StreamingResponse(io.BytesIO(img_bytes), media_type="image/png")
    except Exception as e:
        doc.close()
        raise HTTPException(500, str(e))


# ---------- IMAGE TOOLS ----------

@api_router.post("/image/compress")
async def image_compress(file: UploadFile = File(...), quality: int = Form(70)):
    data = await file.read()
    img = Image.open(io.BytesIO(data)).convert("RGB")
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=max(10, min(95, quality)), optimize=True)
    return _streaming(out.getvalue(), f"compressed-{file.filename}.jpg", "image/jpeg")


@api_router.post("/image/resize")
async def image_resize(file: UploadFile = File(...), width: int = Form(800), height: int = Form(0)):
    data = await file.read()
    img = Image.open(io.BytesIO(data))
    if height <= 0:
        height = int(img.height * (width / img.width))
    img = img.resize((width, height), Image.LANCZOS)
    out = io.BytesIO()
    fmt = "PNG" if img.mode == "RGBA" else "JPEG"
    img.save(out, format=fmt, quality=92)
    ext = "png" if fmt == "PNG" else "jpg"
    return _streaming(out.getvalue(), f"resized.{ext}", f"image/{ext}")


@api_router.post("/image/convert")
async def image_convert(file: UploadFile = File(...), target: str = Form("png")):
    data = await file.read()
    img = Image.open(io.BytesIO(data))
    target = target.lower()
    fmt_map = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "webp": "WEBP", "bmp": "BMP"}
    fmt = fmt_map.get(target, "PNG")
    if fmt == "JPEG":
        img = img.convert("RGB")
    out = io.BytesIO()
    img.save(out, format=fmt)
    return _streaming(out.getvalue(), f"converted.{target}", f"image/{target}")


# ---------- QR CODE ----------

@api_router.post("/qr/generate")
async def qr_generate(data: str = Form(...), size: int = Form(10), color: str = Form("#000000"), bg: str = Form("#FFFFFF")):
    try:
        qr = qrcode.QRCode(box_size=size, border=2)
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color=color, back_color=bg).convert("RGB")
        out = io.BytesIO()
        img.save(out, format="PNG")
        return _streaming(out.getvalue(), "qr.png", "image/png")
    except Exception as e:
        raise HTTPException(500, str(e))


# ---------- AI: PDF SUMMARIZE & CHAT ----------

@api_router.post("/ai/summarize")
async def ai_summarize(file: UploadFile = File(...)):
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
    except Exception as e:
        raise HTTPException(500, f"AI lib missing: {e}")
    data = await file.read()
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        text = ""
        for page in doc:
            text += page.get_text() + "\n"
        doc.close()
        text = text.strip()
        if not text:
            raise HTTPException(400, "No extractable text in PDF.")
        # Truncate for token safety
        text = text[:25000]
        chat = LlmChat(
            api_key=os.environ["EMERGENT_LLM_KEY"],
            session_id=f"sum-{uuid.uuid4()}",
            system_message="You are a precise document summarizer. Produce a clean markdown summary with: a 2-sentence overview, key points (bullets), and conclusions."
        ).with_model("openai", "gpt-4o-mini")
        msg = UserMessage(text=f"Summarize this PDF content:\n\n{text}")
        result = await chat.send_message(msg)
        return {"summary": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"AI summary failed: {str(e)}")


@api_router.post("/ai/chat-pdf")
async def ai_chat_pdf(
    file: UploadFile = File(...),
    question: str = Form(...),
    session_id: Optional[str] = Form(None)
):
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
    except Exception as e:
        raise HTTPException(500, f"AI lib missing: {e}")
    data = await file.read()
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        text = ""
        for page in doc:
            text += page.get_text() + "\n"
        doc.close()
        text = text[:25000]
        sid = session_id or f"chat-{uuid.uuid4()}"
        chat = LlmChat(
            api_key=os.environ["EMERGENT_LLM_KEY"],
            session_id=sid,
            system_message=f"You are a helpful assistant. Answer based on the document below. If unrelated, say so politely.\n\nDOCUMENT:\n{text}"
        ).with_model("openai", "gpt-4o-mini")
        result = await chat.send_message(UserMessage(text=question))
        return {"answer": result, "session_id": sid}
    except Exception as e:
        raise HTTPException(500, f"AI chat failed: {str(e)}")


# ---------- INCLUDE ROUTER ----------

app.include_router(api_router)

# Extra tools (PDF page ops, conversions, OCR, archives, image extras, barcode)
from extra_tools import router as extra_router  # noqa: E402
app.include_router(extra_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
