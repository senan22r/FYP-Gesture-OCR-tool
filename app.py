import base64
import ctypes
import datetime
import io
import json
import os
import re
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
from typing import List, Optional, Tuple
from PIL import ImageEnhance, ImageGrab, ImageTk
from PySide6 import QtCore, QtGui, QtWidgets
from tinydb import TinyDB, Query
import pyperclip
from pynput import keyboard

import offline

# Global app state shared across UI, overlay, and worker threads.
system_on = False
overlay_window: Optional[tk.Toplevel] = None
overlay_root: Optional[tk.Tk] = None
last_capture_start: Optional[float] = None
window: Optional[QtWidgets.QMainWindow] = None
qt_app: Optional[QtWidgets.QApplication] = None
toggle_btn: Optional[QtWidgets.QPushButton] = None
status_lbl: Optional[QtWidgets.QLabel] = None
ocr_mode_combo: Optional[QtWidgets.QComboBox] = None
selection_mode_combo: Optional[QtWidgets.QComboBox] = None
text_mode_combo: Optional[QtWidgets.QComboBox] = None
source_mode_combo: Optional[QtWidgets.QComboBox] = None
text_mode_frame: Optional[QtWidgets.QWidget] = None
selection_frame: Optional[QtWidgets.QWidget] = None
source_mode_frame: Optional[QtWidgets.QWidget] = None
sessions_tree: Optional[QtWidgets.QTreeWidget] = None
entries_doc: Optional[QtWidgets.QTextEdit] = None
session_title_input: Optional[QtWidgets.QLineEdit] = None
doc_search_input: Optional[QtWidgets.QLineEdit] = None
doc_search_status_lbl: Optional[QtWidgets.QLabel] = None
current_session_id: Optional[int] = None
doc_search_matches: List[Tuple[int, int]] = []
doc_search_index: int = -1
DB_PATH = "OCR_DataBase.json"
db = TinyDB(DB_PATH)
sessions_db = db.table("sessions")


class UiInvoker(QtCore.QObject):
    # Qt signal bridge so background threads can safely update UI widgets.
    invoke_signal = QtCore.Signal(object)

    def __init__(self):
        super().__init__()
        self.invoke_signal.connect(self._run)

    @QtCore.Slot(object)
    def _run(self, callback):
        callback()


ui_invoker: Optional[UiInvoker] = None


def invoke_on_ui(callback):
    # Run callback on UI thread (or directly if UI bridge is not ready yet).
    if ui_invoker is None:
        callback()
        return
    ui_invoker.invoke_signal.emit(callback)


PYDRACULA_STYLESHEET = """
QMainWindow {
    background-color: #282a36;
}
QWidget {
    color: #f8f8f2;
    font-family: "Segoe UI";
    font-size: 10pt;
}
QWidget#appRoot {
    background-color: qlineargradient(
        x1:0, y1:0, x2:1, y2:1,
        stop:0 #141722,
        stop:0.28 #2d3551,
        stop:0.62 #252f47,
        stop:0.85 #1d2437,
        stop:1 #11141f
    );
}
QGroupBox {
    border: 1px solid #44475a;
    border-radius: 8px;
    margin-top: 10px;
    padding: 10px;
    background-color: #2f3242;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: #f8f8f2;
}
QLabel {
    color: #f8f8f2;
}
QPushButton {
    background-color: #44475a;
    color: #f8f8f2;
    border: 1px solid #6272a4;
    border-radius: 6px;
    padding: 6px 10px;
}
QPushButton:hover {
    background-color: #6272a4;
}
QLineEdit, QComboBox, QTextEdit, QPlainTextEdit, QTreeWidget {
    background-color: #1e1f29;
    color: #f8f8f2;
    border: 1px solid #44475a;
    border-radius: 6px;
    selection-background-color: #6272a4;
    selection-color: #f8f8f2;
}
QHeaderView::section {
    background-color: #44475a;
    color: #f8f8f2;
    border: none;
    padding: 6px;
}
QScrollBar:vertical, QScrollBar:horizontal {
    background: #282a36;
}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: #6272a4;
    border-radius: 4px;
}
QTabWidget::pane {
    border: 1px solid #44475a;
    border-radius: 10px;
    background-color: #2f3242;
    margin-top: 8px;
}
QTabBar::tab {
    background-color: #2c2f3f;
    color: #d6d7e4;
    border: 1px solid #44475a;
    border-top-left-radius: 10px;
    border-top-right-radius: 10px;
    min-width: 118px;
    min-height: 38px;
    margin: 0 6px 0 0;
    padding: 5px 10px;
    font-size: 12px;
    font-weight: 600;
}
QTabBar::tab:selected {
    background-color: #6a7dc2;
    color: #f8f8f2;
    border-color: #93a6df;
}
QTabBar::tab:hover {
    background-color: #3d4258;
    color: #f8f8f2;
}
QPushButton#powerButton {
    background-color: #1f2130;
    border: 6px solid #6272a4;
    border-radius: 130px;
    color: #8be9fd;
    font-size: 92px;
    font-weight: 700;
}
QPushButton#powerButton:hover {
    background-color: #2a2d3f;
    border-color: #8be9fd;
}
QPushButton#powerButton[active="true"] {
    color: #50fa7b;
    border-color: #50fa7b;
}
"""

# Light theme alternative.
LIGHT_STYLESHEET = """
QMainWindow {
    background-color: #f5f7fb;
}
QWidget {
    color: #1f2937;
    font-family: "Segoe UI";
    font-size: 10pt;
}
QWidget#appRoot {
    background-color: qlineargradient(
        x1:0, y1:0, x2:1, y2:1,
        stop:0 #ffffff,
        stop:0.26 #edf4ff,
        stop:0.58 #dceaff,
        stop:0.82 #cfe0fb,
        stop:1 #bfd4f5
    );
}
QGroupBox {
    border: 1px solid #d6dbe6;
    border-radius: 8px;
    margin-top: 10px;
    padding: 10px;
    background-color: #ffffff;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: #1f2937;
}
QLabel {
    color: #1f2937;
}
QPushButton {
    background-color: #e8edf7;
    color: #1f2937;
    border: 1px solid #c1c9dc;
    border-radius: 6px;
    padding: 6px 10px;
}
QPushButton:hover {
    background-color: #d7e1f5;
}
QLineEdit, QComboBox, QTextEdit, QPlainTextEdit, QTreeWidget {
    background-color: #ffffff;
    color: #1f2937;
    border: 1px solid #d6dbe6;
    border-radius: 6px;
    selection-background-color: #b9c9ee;
    selection-color: #1f2937;
}
QComboBox {
    background-color: #ffffff;
    color: #1f2937;
    padding-right: 22px;
}
QComboBox::drop-down {
    border: none;
    width: 22px;
    background-color: #edf2fb;
}
QComboBox QAbstractItemView {
    background-color: #ffffff;
    color: #1f2937;
    border: 1px solid #cfd8ec;
    selection-background-color: #c6d6f4;
    selection-color: #1f2937;
    outline: 0;
}
QHeaderView::section {
    background-color: #e8edf7;
    color: #1f2937;
    border: none;
    padding: 6px;
}
QScrollBar:vertical, QScrollBar:horizontal {
    background: #f5f7fb;
}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: #a2b3d9;
    border-radius: 4px;
}
QTabWidget::pane {
    border: 1px solid #d6dbe6;
    border-radius: 10px;
    background-color: #ffffff;
    margin-top: 8px;
}
QTabBar::tab {
    background-color: #edf1fb;
    color: #3f4f69;
    border: 1px solid #d6dbe6;
    border-top-left-radius: 10px;
    border-top-right-radius: 10px;
    min-width: 118px;
    min-height: 38px;
    margin: 0 6px 0 0;
    padding: 5px 10px;
    font-size: 12px;
    font-weight: 600;
}
QTabBar::tab:selected {
    background-color: #c9d9f5;
    color: #1f2937;
    border-color: #9fb5de;
}
QTabBar::tab:hover {
    background-color: #dbe5f8;
    color: #1f2937;
}
QPushButton#powerButton {
    background-color: #ffffff;
    border: 6px solid #5e78b6;
    border-radius: 130px;
    color: #4c70c5;
    font-size: 92px;
    font-weight: 700;
}
QPushButton#powerButton:hover {
    background-color: #f4f8ff;
    border-color: #4c70c5;
}
QPushButton#powerButton[active="true"] {
    color: #1f9d55;
    border-color: #1f9d55;
}
"""


def apply_theme(theme_name: str):
    # Apply dark or light stylesheet at runtime.
    if qt_app is None:
        return
    if theme_name == "Light":
        qt_app.setStyleSheet(LIGHT_STYLESHEET)
    else:
        qt_app.setStyleSheet(PYDRACULA_STYLESHEET)


VISION_ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"
DEFAULT_KEY_FILE = "vision_key.txt"


def _load_api_key() -> str:
    # Read Cloud Vision key from env first, then fallback key file.
    env_key = os.getenv("VISION_API_KEY", "").strip()
    if env_key:
        return env_key
    key_file = os.getenv("VISION_API_KEY_FILE", "").strip() or DEFAULT_KEY_FILE
    try:
        with open(key_file, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def _encode_image_to_base64(image) -> str:
    # Convert PIL image to base64 PNG for API payloads.
    with io.BytesIO() as output:
        image.save(output, format="PNG")
        return base64.b64encode(output.getvalue()).decode("utf-8")


def _call_vision_api(image) -> dict:
    # Call Google Vision TEXT_DETECTION for a screenshot/image.
    api_key = _load_api_key()
    if not api_key:
        raise RuntimeError(
            "Missing API key: set VISION_API_KEY or put it in vision_key.txt."
        )
    payload = {
        "requests": [
            {
                "image": {"content": _encode_image_to_base64(image)},
                "features": [{"type": "TEXT_DETECTION"}],
            }
        ]
    }
    data = json.dumps(payload).encode("utf-8")
    url = f"{VISION_ENDPOINT}?key={api_key}"
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            details = e.read().decode("utf-8")
        except Exception:
            details = str(e)
        raise RuntimeError(f"Vision API error: {details}") from e
    except Exception as e:
        raise RuntimeError(f"Vision API request failed: {e}") from e


def _assign_line_and_word_numbers(words):
    # Group OCR words into lines and assign deterministic word order.
    grouped = {}
    for word in words:
        key = (word["block_num"], word["par_num"])
        grouped.setdefault(key, []).append(word)
    updated = []
    for _key, group in grouped.items():
        group_sorted = sorted(group, key=lambda w: (w["top"], w["left"]))
        lines = []
        current = []
        current_center = None
        current_height = None
        for word in group_sorted:
            center = (word["top"] + word["bottom"]) / 2
            height = max(1, word["bottom"] - word["top"])
            threshold = max(8, height * 0.6)
            if current and abs(center - current_center) > max(
                threshold, (current_height or 0) * 0.6
            ):
                lines.append(current)
                current = [word]
                current_center = center
                current_height = height
            else:
                current.append(word)
                if current_center is None:
                    current_center = center
                    current_height = height
                else:
                    current_center = (
                        (current_center * (len(current) - 1) + center) / len(current)
                    )
                    current_height = max(current_height or 0, height)
        if current:
            lines.append(current)
        for line_num, line_words in enumerate(lines, start=1):
            line_words.sort(key=lambda w: w["left"])
            for word_num, word in enumerate(line_words, start=1):
                word["line_num"] = line_num
                word["word_num"] = word_num
                updated.append(word)
    return updated


def get_ocr_data(image, display_width, display_height):
    # Parse Vision response into normalized word boxes in display coordinates.
    response = _call_vision_api(image)
    result = response.get("responses", [{}])[0]
    full = result.get("fullTextAnnotation")
    if not full:
        return []
    words_raw = []
    pages = full.get("pages", [])
    for page in pages:
        for block_idx, block in enumerate(page.get("blocks", []), start=1):
            for para_idx, paragraph in enumerate(block.get("paragraphs", []), start=1):
                for word in paragraph.get("words", []):
                    text = "".join(
                        symbol.get("text", "") for symbol in word.get("symbols", [])
                    )
                    text = text.strip()
                    if not text:
                        continue
                    vertices = word.get("boundingBox", {}).get("vertices", [])
                    xs = [v.get("x", 0) for v in vertices]
                    ys = [v.get("y", 0) for v in vertices]
                    left = min(xs) if xs else 0
                    right = max(xs) if xs else 0
                    top = min(ys) if ys else 0
                    bottom = max(ys) if ys else 0
                    conf = int(word.get("confidence", 0) * 100)
                    words_raw.append(
                        {
                            "text": text,
                            "left": left,
                            "top": top,
                            "width": max(0, right - left),
                            "height": max(0, bottom - top),
                            "right": right,
                            "bottom": bottom,
                            "conf": conf,
                            "block_num": block_idx,
                            "par_num": para_idx,
                        }
                    )
    words = _assign_line_and_word_numbers(words_raw)
    img_width, img_height = image.size
    scale_x = display_width / img_width if img_width else 1.0
    scale_y = display_height / img_height if img_height else 1.0
    for word in words:
        word["left"] = int(word["left"] * scale_x)
        word["right"] = int(word["right"] * scale_x)
        word["top"] = int(word["top"] * scale_y)
        word["bottom"] = int(word["bottom"] * scale_y)
        word["width"] = max(0, word["right"] - word["left"])
        word["height"] = max(0, word["bottom"] - word["top"])
    return words


class _OnlineProvider:
    # Keeps same provider interface used by OCR mode selector.
    @staticmethod
    def get_ocr_data(image, display_width, display_height):
        return get_ocr_data(image, display_width, display_height)


OCR_MODES = {
    "Online (Cloud Vision)": _OnlineProvider,
    "Offline (Tesseract)": offline,
}
DEFAULT_OCR_MODE = "Online (Cloud Vision)"
SELECTION_MODES = [
    "Native selection",
    "Box selection",
]
DEFAULT_SELECTION_MODE = "Native selection"
TEXT_MODES = ["Original", "Light Clean", "Deep Clean"]
DEFAULT_TEXT_MODE = "Original"
SOURCE_MODES = ["Off", "On"]
DEFAULT_SOURCE_MODE = "Off"
TRANSLATE_MODES = {
    "off": "Trans: Off",
    "include_original": "Trans: +Orig",
    "translation_only": "Trans Only",
}
DEFAULT_TRANSLATE_MODE = "off"
GEMINI_MODEL_CANDIDATES = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash-latest",
    "gemini-1.5-flash",
]
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass


def get_active_provider():
    # Current OCR engine from dropdown (or default).
    if ocr_mode_combo is None:
        return OCR_MODES[DEFAULT_OCR_MODE], DEFAULT_OCR_MODE
    name = ocr_mode_combo.currentText().strip() or DEFAULT_OCR_MODE
    return OCR_MODES.get(name, OCR_MODES[DEFAULT_OCR_MODE]), name


def get_selection_mode():
    # Current selection behavior mode.
    if selection_mode_combo is None:
        return DEFAULT_SELECTION_MODE
    return selection_mode_combo.currentText().strip() or DEFAULT_SELECTION_MODE


def get_text_mode():
    # Current text formatting mode.
    if text_mode_combo is None:
        return DEFAULT_TEXT_MODE
    return text_mode_combo.currentText().strip() or DEFAULT_TEXT_MODE


def get_source_mode():
    # Current source-guess mode (On/Off).
    if source_mode_combo is None:
        return DEFAULT_SOURCE_MODE
    return source_mode_combo.currentText().strip() or DEFAULT_SOURCE_MODE


def show_temporary_status(message: str, duration_ms: int = 3500):
    # Temporarily show a short status message, then restore previous label.
    if status_lbl is None:
        return
    previous = status_lbl.text()
    status_lbl.setText(message)

    def _restore():
        if status_lbl is None:
            return
        if status_lbl.text() == message:
            status_lbl.setText(previous)

    QtCore.QTimer.singleShot(duration_ms, _restore)


def _light_clean_text(text: str) -> str:
    # Light cleanup: normalize spacing and preserve line structure.
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cleaned_lines = []
    blank_seen = False
    for line in lines:
        # Keep line structure but normalize spacing.
        fixed = re.sub(r"[ \t]+", " ", line).strip()
        if fixed == "":
            if not blank_seen:
                cleaned_lines.append("")
            blank_seen = True
        else:
            cleaned_lines.append(fixed)
            blank_seen = False
    return "\n".join(cleaned_lines).strip()


def _deep_clean_text(text: str) -> str:
    # Local deep cleanup: merge wrapped lines and normalize punctuation spacing.
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    paragraphs = []
    current = []
    for raw in lines:
        line = re.sub(r"[ \t]+", " ", raw).strip()
        if not line:
            if current:
                paragraphs.append(current)
                current = []
            continue
        current.append(line)
    if current:
        paragraphs.append(current)

    merged = []
    for para_lines in paragraphs:
        para = ""
        for line in para_lines:
            if para.endswith("-"):
                para = para[:-1] + line
            elif para:
                para += " " + line
            else:
                para = line
        para = re.sub(r"\s+([,.;:!?])", r"\1", para)
        para = re.sub(r"([,.;:!?])([^\s])", r"\1 \2", para)
        para = re.sub(r"\s+", " ", para).strip()
        if para:
            merged.append(para)
    return "\n\n".join(merged).strip()


def apply_text_mode(text: str) -> str:
    # Apply selected formatting mode, with fallback when Gemini fails.
    mode = get_text_mode()
    if mode == "Light Clean":
        return _light_clean_text(text)
    if mode == "Deep Clean":
        try:
            return deep_clean_with_gemini(text)
        except Exception as e:
            print(f"Deep Clean fallback (local): {e}")
            invoke_on_ui(
                lambda: show_temporary_status(
                    "Deep Clean unavailable: using local cleanup"
                )
            )
            return _deep_clean_text(text)
    return text.strip()


def _load_gemini_api_key() -> str:
    # Read Gemini key from env/file candidates.
    env_candidates = [
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "VISION_API_KEY",
    ]
    for key_name in env_candidates:
        val = os.getenv(key_name, "").strip()
        if val:
            return val
    file_candidates = [
        os.getenv("GEMINI_API_KEY_FILE", "").strip(),
        "gemini_key.txt",
        os.getenv("VISION_API_KEY_FILE", "").strip(),
        "vision_key.txt",
    ]
    for path in file_candidates:
        if not path:
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                key = f.read().strip()
                if key:
                    return key
        except Exception:
            continue
    return ""


def _call_gemini_parts(parts) -> str:
    # Generic Gemini caller supporting text-only and multimodal prompts.
    api_key = _load_gemini_api_key()
    if not api_key:
        raise RuntimeError("Missing Gemini API key.")
    payload = {
        "contents": [
            {
                "parts": parts,
            }
        ]
    }
    data = json.dumps(payload).encode("utf-8")
    last_error = None
    for model_name in GEMINI_MODEL_CANDIDATES:
        req = urllib.request.Request(
            f"{GEMINI_API_BASE}/{model_name}:generateContent?key={api_key}",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            candidates = body.get("candidates", [])
            if not candidates:
                last_error = f"{model_name}: no candidates"
                continue
            parts = candidates[0].get("content", {}).get("parts", [])
            output = "\n".join((p.get("text", "") for p in parts)).strip()
            if output:
                return output
            last_error = f"{model_name}: empty output"
        except urllib.error.HTTPError as e:
            details = e.read().decode("utf-8", errors="replace")
            if e.code == 404:
                last_error = f"{model_name}: not found"
                continue
            raise RuntimeError(f"Gemini HTTP error ({model_name}): {details}") from e
        except Exception as e:
            raise RuntimeError(f"Gemini request failed ({model_name}): {e}") from e
    raise RuntimeError(f"Gemini unavailable. Last error: {last_error}")


def _call_gemini_prompt(prompt: str) -> str:
    # Convenience wrapper for text-only Gemini requests.
    return _call_gemini_parts([{"text": prompt}])


def translate_with_gemini(text: str) -> str:
    # Translation helper used by overlay mini toolbar options.
    if not text.strip():
        return ""
    prompt = (
        "Translate the following text to natural English. "
        "Return only the translated text.\n\n"
        f"{text}"
    )
    return _call_gemini_prompt(prompt)


def deep_clean_with_gemini(text: str) -> str:
    # Ask Gemini to clean OCR text while preserving meaning/language.
    if not text.strip():
        return ""
    prompt = (
        "Clean OCR text for readability while preserving original meaning and language. "
        "Fix spacing, punctuation spacing, broken line wraps, and paragraph structure. "
        "Do not translate, do not summarize, and do not add information. "
        "Return only cleaned text.\n\n"
        f"{text}"
    )
    cleaned = _call_gemini_prompt(prompt)
    return cleaned.strip()


def _extract_url_candidate(value: str) -> str:
    # Extract likely URL/domain from noisy OCR text.
    if not value:
        return ""
    compact = re.sub(r"\s+", "", value).strip()
    match = re.search(
        r"(https?://[^\s]+|www\.[^\s]+|(?:[a-z][a-z0-9-]*\.)+[a-z]{2,}(?:/[^\s]*)?)",
        compact,
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    raw = match.group(0).rstrip(").,;:!?")
    # Avoid false positives like "1.pdf" when no real URL context exists.
    if "://" not in raw and "/" not in raw and raw.lower().endswith(".pdf"):
        return ""
    return raw


def _extract_url_from_top_strip_ocr(image) -> str:
    # OCR the top strip (address-bar area) to find source URL quickly.
    try:
        strip_h = max(110, int(image.height * 0.20))
        top_strip = image.crop((0, 0, image.width, strip_h))
        top_strip = ImageEnhance.Contrast(top_strip).enhance(1.8)
        top_strip = ImageEnhance.Sharpness(top_strip).enhance(1.7)
        top_strip = top_strip.resize(
            (int(top_strip.width * 1.8), int(top_strip.height * 1.8))
        )
        response = _call_vision_api(top_strip)
        full = response.get("responses", [{}])[0].get("fullTextAnnotation", {})
        raw_text = full.get("text", "")
        if not raw_text:
            return ""
        compact = re.sub(r"\s+", "", raw_text)
        compact = compact.replace("https:/", "https://").replace("http:/", "http://")
        return _extract_url_candidate(compact)
    except Exception:
        return ""


def _guess_source_label(text: str, image=None) -> str:
    # Build best source guess: URL first, then richer Gemini inference.
    if not text.strip() and image is None:
        return "Unknown"
    text_url = _extract_url_candidate(text)
    if image is not None:
        try:
            full_img = image.copy()
            top_ocr_url = _extract_url_from_top_strip_ocr(full_img)
            if top_ocr_url:
                return top_ocr_url
            img = image.copy()
            img.thumbnail((1280, 1280))
            image_b64 = _encode_image_to_base64(img)
            # First pass: focus on top browser area to extract URL/domain if visible.
            top_h_full = max(110, int(full_img.height * 0.20))
            top_strip = full_img.crop((0, 0, full_img.width, top_h_full))
            # Enhance contrast slightly for tiny address-bar text.
            top_strip = ImageEnhance.Contrast(top_strip).enhance(1.6)
            top_strip = ImageEnhance.Sharpness(top_strip).enhance(1.5)
            top_strip = top_strip.resize(
                (int(top_strip.width * 1.6), int(top_strip.height * 1.6))
            )
            top_b64 = _encode_image_to_base64(top_strip)
            url_parts = [
                {
                    "text": (
                        "Read the browser address bar from this screenshot top strip. "
                        "If a URL or domain is visible, return only that URL/domain. "
                        "If not visible, return exactly: Unknown."
                    )
                },
                {"inline_data": {"mime_type": "image/png", "data": top_b64}},
            ]
            url_guess = _call_gemini_parts(url_parts).strip()
            url_from_bar = _extract_url_candidate(url_guess)

            parts = [
                {
                    "text": (
                        "Identify the source as specifically as possible from this screenshot. "
                        "Prefer: document/page title + site/platform + URL/domain when visible. "
                        "Return exactly one line in this format:\n"
                        "<title or page name> | <site/platform> | <url-or-domain>\n"
                        "If a field is unknown use Unknown. No extra text."
                    )
                },
                {"inline_data": {"mime_type": "image/png", "data": image_b64}},
                {
                    "text": (
                        f"OCR text context (partial):\n{text[:2000]}\n\n"
                        f"Address bar guess: {url_from_bar or text_url or 'Unknown'}"
                    )
                },
            ]
            source = _call_gemini_parts(parts).strip()
            if source and source.lower() != "unknown":
                url_candidate = _extract_url_candidate(source) or url_from_bar or text_url
                # Prefer more specific source string; ensure URL/domain gets included when found.
                if url_candidate and url_candidate not in source:
                    source = f"{source} | {url_candidate}"
                return source
        except Exception:
            pass
    if text_url:
        return text_url
    prompt = (
        "You are given OCR text from a screenshot. "
        "Guess the likely source (website, app, document name, or publication) if possible. "
        "If uncertain, return exactly: Unknown. "
        "Return only one short source label and nothing else.\n\n"
        f"{text[:3000]}"
    )
    source = _call_gemini_prompt(prompt).strip()
    return source or "Unknown"


def _normalize_source_label(source_label: str) -> str:
    # Normalize source output into a stable readable format.
    cleaned = (source_label or "").strip().strip("`").strip()
    if not cleaned:
        return "Unknown"
    # Preserve structured rich guess lines from Gemini.
    if "|" in cleaned:
        parts = [p.strip() for p in cleaned.split("|")]
        parts = [p for p in parts if p]
        cleaned = " | ".join(parts)
    match = re.search(
        r"(https?://[^\s]+|(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s]*)?)",
        cleaned,
        flags=re.IGNORECASE,
    )
    if match:
        raw = match.group(0).rstrip(").,;:!?")
        if raw.lower().startswith("www."):
            return raw[4:].lower()
        if raw.lower().startswith("http://") or raw.lower().startswith("https://"):
            try:
                from urllib.parse import urlparse
                parsed = urlparse(raw)
                scheme = parsed.scheme.lower() or "https"
                host = (parsed.netloc or "").lower().lstrip("www.")
                path = parsed.path or ""
                query = f"?{parsed.query}" if parsed.query else ""
                if host:
                    return f"{scheme}://{host}{path}{query}"
                return raw
            except Exception:
                return raw
        # Keep non-scheme URL-like strings as-is if they include a path.
        if "/" in raw:
            return raw
        return raw.lower()
    lowered = cleaned.lower()
    if "google docs" in lowered or lowered in {"gdocs", "docs"}:
        return "docs.google.com"
    return cleaned


def get_or_create_default_session() -> int:
    # Use first existing session or create default Session 1.
    existing = sessions_db.all()
    if existing:
        return existing[0].doc_id
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return sessions_db.insert({"title": "Session 1", "created_at": now})


def ensure_legacy_session():
    # Ensure legacy session exists for old rows without session_id.
    legacy = sessions_db.get(Query().title == "Legacy")
    if legacy:
        return legacy.doc_id
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return sessions_db.insert({"title": "Legacy", "created_at": now})


def migrate_legacy_entries():
    # Migrate old db rows that were saved before session support.
    rows = db.all()
    if not rows:
        return
    needs_migration = any("session_id" not in row for row in rows)
    if not needs_migration:
        return
    legacy_id = ensure_legacy_session()
    for row in rows:
        if "session_id" not in row:
            db.update({"session_id": legacy_id}, doc_ids=[row.doc_id])


def create_session(title: Optional[str] = None) -> int:
    # Create a new session record with timestamp.
    existing = sessions_db.all()
    next_index = len(existing) + 1
    session_title = title or f"Session {next_index}"
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return sessions_db.insert({"title": session_title, "created_at": now})


def refresh_sessions_list(select_id: Optional[int] = None):
    # Rebuild left sessions list and optionally select one.
    if sessions_tree is None:
        return
    sessions_tree.clear()
    sessions = sessions_db.all()
    sessions_sorted = sorted(sessions, key=lambda r: r.get("created_at", ""))
    for doc in sessions_sorted:
        item = QtWidgets.QTreeWidgetItem(
            [doc.get("title", ""), doc.get("created_at", "")]
        )
        item.setData(0, QtCore.Qt.UserRole, doc.doc_id)
        sessions_tree.addTopLevelItem(item)
    if select_id is not None:
        for idx in range(sessions_tree.topLevelItemCount()):
            item = sessions_tree.topLevelItem(idx)
            if int(item.data(0, QtCore.Qt.UserRole)) == select_id:
                sessions_tree.setCurrentItem(item)
                sessions_tree.scrollToItem(item)
                break


def on_session_select(_event=None):
    # Load selected session metadata and document text.
    global current_session_id
    if sessions_tree is None:
        return
    selected = sessions_tree.selectedItems()
    if not selected:
        return
    item = selected[0]
    current_session_id = int(item.data(0, QtCore.Qt.UserRole))
    if session_title_input is not None:
        session_title_input.setText(item.text(0))
    refresh_entries()


def _compose_session_document(session_id: int) -> str:
    # Build a full session document string from raw captured rows.
    rows = db.search(Query().session_id == session_id)
    rows_sorted = sorted(rows, key=lambda r: r.get("timestamp", ""))
    parts = []
    for doc in rows_sorted:
        ts = doc.get("timestamp", "")
        full_text = doc.get("extracted_text", "")
        if ts:
            parts.append(f"[{ts}]\n{full_text}".strip())
        else:
            parts.append(full_text.strip())
    return "\n\n\n".join(p for p in parts if p)


def _save_text_to_session(session_id: int, text: str) -> int:
    # Save one entry row and mirror into editable session document if present.
    if not text.strip():
        return -1
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry_id = db.insert({
        "timestamp": ts,
        "extracted_text": text,
        "session_id": session_id,
    })
    session_doc = sessions_db.get(doc_id=session_id)
    existing = (session_doc or {}).get("document_text")
    if isinstance(existing, str):
        entry = f"[{ts}]\n{text}".strip()
        if existing.strip():
            updated = f"{existing.rstrip()}\n\n\n{entry}"
        else:
            updated = entry
        sessions_db.update({"document_text": updated}, doc_ids=[session_id])
    return entry_id


def save_to_db(text: str) -> int:
    # Save capture to current session.
    if not text.strip():
        return -1
    global current_session_id
    if current_session_id is None:
        current_session_id = get_or_create_default_session()
    return _save_text_to_session(current_session_id, text)


def _append_source_to_saved_entry(session_id: int, entry_id: int, source_label: str):
    # Append or update source guess line inside the same saved capture entry.
    if entry_id <= 0:
        return
    row = db.get(doc_id=entry_id)
    if not row or row.get("session_id") != session_id:
        return
    source_line = f"Source Guess: {source_label}".strip()
    original_text = row.get("extracted_text", "")
    if re.search(r"(?im)^source(?: guess)?:\s*", original_text):
        updated_text = re.sub(
            r"(?im)^source(?: guess)?:\s*.*$",
            source_line,
            original_text,
        ).strip()
    else:
        updated_text = f"{original_text.rstrip()}\n{source_line}".strip()
    db.update({"extracted_text": updated_text}, doc_ids=[entry_id])

    session_doc = sessions_db.get(doc_id=session_id)
    existing = (session_doc or {}).get("document_text")
    if isinstance(existing, str):
        updated_doc = existing.rstrip()
        if not updated_doc:
            updated_doc = source_line
        else:
            updated_doc = f"{updated_doc}\n{source_line}"
        sessions_db.update({"document_text": updated_doc}, doc_ids=[session_id])


def _save_source_note_async(text: str, image, session_id: int, entry_id: int):
    # Run source guessing in background so copy/save path stays fast.
    def worker():
        try:
            # Source guessing can be slow, so this runs fully off the main UI thread.
            source_label = _normalize_source_label(_guess_source_label(text, image))
            _append_source_to_saved_entry(session_id, entry_id, source_label)
            # Refresh only if user is still viewing the same session.
            invoke_on_ui(lambda: refresh_entries() if current_session_id == session_id else None)
        except Exception as e:
            print(f"Source guess failed: {e}")

    threading.Thread(target=worker, daemon=True).start()




def reconstruct_text_from_words(words):
    """Rebuild text using line/word ordering."""
    if not words:
        return ""
    words_sorted = sorted(
        words,
        key=lambda word: (word['block_num'], word['par_num'], word['line_num'], word['word_num'])
    )
    lines = []
    current_key = None
    current_words = []
    for word in words_sorted:
        key = (word['block_num'], word['par_num'], word['line_num'])
        if current_key is None or key != current_key:
            if current_words:
                lines.append(' '.join(current_words))
            current_words = [word['text']]
            current_key = key
        else:
            current_words.append(word['text'])
    if current_words:
        lines.append(' '.join(current_words))
    return '\n'.join(lines).strip()


def point_in_rect(x, y, rect):
    """Check if point inside rectangle"""
    return rect['left'] <= x <= rect['right'] and rect['top'] <= y <= rect['bottom']


def rects_intersect(rect1, rect2):
    """Check if rectangles overlap"""
    return not (rect1['right'] < rect2['left'] or rect1['left'] > rect2['right'] or rect1['bottom'] < rect2['top'] or
                rect1['top'] > rect2['bottom'])


def find_word_for_point(words, x, y):
    # Pick nearest containing word; fallback to nearest by center distance.
    containing = [w for w in words if point_in_rect(x, y, w)]
    if containing:
        containing.sort(
            key=lambda w: (w["right"] - w["left"]) * (w["bottom"] - w["top"])
        )
        return containing[0]
    closest = None
    for w in words:
        cx = (w["left"] + w["right"]) / 2
        cy = (w["top"] + w["bottom"]) / 2
        dist = (cx - x) ** 2 + (cy - y) ** 2
        if closest is None or dist < closest[0]:
            closest = (dist, w)
    return closest[1] if closest else None


def build_paragraph_bounds(words, block_num, par_num):
    # Return bounding rectangle for one paragraph.
    para_words = [
        w for w in words if w["block_num"] == block_num and w["par_num"] == par_num
    ]
    if not para_words:
        return None
    left = min(w["left"] for w in para_words)
    right = max(w["right"] for w in para_words)
    top = min(w["top"] for w in para_words)
    bottom = max(w["bottom"] for w in para_words)
    return left, top, right, bottom


def build_line_index(words):
    # Index words by line and compute vertical bounds for each line.
    line_map = {}
    line_bounds = {}
    for word in words:
        key = (word['block_num'], word['par_num'], word['line_num'])
        line_map.setdefault(key, []).append(word)
    for key, line_words in line_map.items():
        line_words.sort(key=lambda word: word['word_num'])
        top = min(word['top'] for word in line_words)
        bottom = max(word['bottom'] for word in line_words)
        line_bounds[key] = (top, bottom)
    line_order = sorted(line_map.keys())
    return line_order, line_map, line_bounds


def find_line_for_y(line_order, line_bounds, y):
    # Map a Y coordinate to best line key with small tolerance.
    tol = 8
    for key in line_order:
        top, bottom = line_bounds[key]
        if top - tol <= y <= bottom + tol:
            return key
    return min(
        line_order,
        key=lambda k: abs(((line_bounds[k][0] + line_bounds[k][1]) / 2) - y),
    )


def pick_word_index_by_x(line_words, x, pick):
    # Find start/end word index in a line for drag selection.
    centers = [((w['left'] + w['right']) / 2) for w in line_words]
    if pick == "start":
        for i, cx in enumerate(centers):
            if cx >= x:
                return i
        return len(line_words) - 1
    for i in range(len(centers) - 1, -1, -1):
        if centers[i] <= x:
            return i
    return 0


def select_words_by_drag(words, start_x, start_y, end_x, end_y, mode):
    # Main selection algorithm for Native and Box modes.
    if not words:
        return []
    if mode == "Box selection":
        left = min(start_x, end_x)
        right = max(start_x, end_x)
        top = min(start_y, end_y)
        bottom = max(start_y, end_y)
        drag_rect = {"left": left, "right": right, "top": top, "bottom": bottom}
        selected = [word for word in words if rects_intersect(word, drag_rect)]
        selected.sort(
            key=lambda word: (word["block_num"], word["par_num"], word["line_num"], word["word_num"])
        )
        return selected
    start_word = find_word_for_point(words, start_x, start_y)
    if start_word is None:
        return []
    block = start_word["block_num"]
    paragraph = start_word["par_num"]
    para_bounds = build_paragraph_bounds(words, block, paragraph)
    if para_bounds is None:
        return []
    left, top, right, bottom = para_bounds
    pad = int(max(8, (right - left) * 0.15))
    valid_left = left - pad
    valid_right = right + pad
    column_words = [
        w for w in words
        if w["block_num"] == block
        and w["left"] >= valid_left and w["right"] <= valid_right
    ]
    line_order, line_map, line_bounds = build_line_index(column_words)
    if not line_order:
        return []
    start_line = find_line_for_y(line_order, line_bounds, start_y)
    end_line = find_line_for_y(line_order, line_bounds, end_y)
    start_line_idx = line_order.index(start_line)
    end_line_idx = line_order.index(end_line)
    if start_line_idx > end_line_idx or (start_line_idx == end_line_idx and end_x < start_x):
        start_x, start_y, end_x, end_y = end_x, end_y, start_x, start_y
        start_line = find_line_for_y(line_order, line_bounds, start_y)
        end_line = find_line_for_y(line_order, line_bounds, end_y)
        start_line_idx = line_order.index(start_line)
        end_line_idx = line_order.index(end_line)
    selected = []
    for idx in range(start_line_idx, end_line_idx + 1):
        key = line_order[idx]
        line_words = sorted(line_map[key], key=lambda word: word["word_num"])
        if not line_words:
            continue
        if idx == start_line_idx and idx == end_line_idx:
            start_i = pick_word_index_by_x(line_words, start_x, "start")
            end_i = pick_word_index_by_x(line_words, end_x, "end")
            if end_i < start_i:
                start_i, end_i = end_i, start_i
            selected.extend(line_words[start_i:end_i + 1])
        elif idx == start_line_idx:
            start_i = pick_word_index_by_x(line_words, start_x, "start")
            selected.extend(line_words[start_i:])
        elif idx == end_line_idx:
            end_i = pick_word_index_by_x(line_words, end_x, "end")
            selected.extend(line_words[:end_i + 1])
        else:
            selected.extend(line_words)
    return selected


def create_overlay():
    # Show frozen screenshot overlay and let user select text visually.
    global overlay_window
    if overlay_window is not None:
        return

    screenshot = ImageGrab.grab()

    overlay_window = tk.Toplevel()
    overlay_window.attributes('-fullscreen', True)
    overlay_window.attributes('-topmost', True)
    overlay_window.attributes('-alpha', 0.99)
    overlay_window.overrideredirect(True)
    overlay_window.configure(cursor="xterm")

    # Fail-safe: keep Escape available even if later UI setup fails.
    def _close_overlay_immediately(_event=None):
        global overlay_window
        if overlay_window is not None:
            overlay_window.destroy()
            overlay_window = None

    overlay_window.bind("<Escape>", _close_overlay_immediately)

    win_width = screenshot.width
    win_height = screenshot.height
    overlay_window.geometry(f"{win_width}x{win_height}+0+0")

    photo = ImageTk.PhotoImage(screenshot)
    canvas = tk.Canvas(overlay_window, highlightthickness=0, cursor="xterm", width=win_width, height=win_height)
    canvas.pack(fill="both", expand=True)
    canvas.create_image(0, 0, anchor="nw", image=photo)
    canvas.image = photo

    border_colors = ["#FFFFFF", "#EDEDED", "#DADADA", "#C7C7C7"]
    for i, color in enumerate(border_colors):
        canvas.create_rectangle(
            i,
            i,
            win_width - 1 - i,
            win_height - 1 - i,
            outline=color,
            width=2 if i == 0 else 1,
        )

    selection_start: Optional[Tuple[int, int]] = None
    selection_end: Optional[Tuple[int, int]] = None
    selected_words = []
    ocr_words = []
    highlight_rects = []
    status_text_id = None
    last_drag_time = 0.0
    box_rect_id = None
    mini_bar_window_id = None
    show_minibar_for_mode = {"value": True}
    mini_bar_drag = {
        "start_x": 0,
        "start_y": 0,
        "start_canvas_x": 0,
        "start_canvas_y": 0,
        "active": False,
        "handled_click": False,
        "pressed_button": None,
    }
    mini_bar_pos = {"x": win_width // 2, "y": 40}
    mini_bar_state = {"translate_mode": DEFAULT_TRANSLATE_MODE}
    mini_bar_hover = {"button": None}
    mini_bar_bounds = {"bar": None, "buttons": {}}
    translate_menu = tk.Menu(overlay_window, tearoff=0)
    selection_menu = tk.Menu(overlay_window, tearoff=0)
    text_mode_menu = tk.Menu(overlay_window, tearoff=0)

    def _selection_label_for_bar() -> str:
        mode = get_selection_mode()
        if mode == "Native selection":
            return "Native"
        if mode == "Box selection":
            return "Box"
        return mode

    def _text_mode_label_for_bar() -> str:
        mode = get_text_mode()
        if mode == "Light Clean":
            return "Light"
        if mode == "Deep Clean":
            return "Deep"
        return mode

    def draw_rounded_rect(x1, y1, x2, y2, radius, fill, outline, width, tags):
        ids = []
        r = min(radius, int((x2 - x1) / 2), int((y2 - y1) / 2))
        ids.append(canvas.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline="", tags=tags))
        ids.append(canvas.create_rectangle(x1, y1 + r, x2, y2 - r, fill=fill, outline="", tags=tags))
        ids.append(canvas.create_arc(x1, y1, x1 + 2 * r, y1 + 2 * r, start=90, extent=90, style="pieslice", fill=fill, outline="", tags=tags))
        ids.append(canvas.create_arc(x2 - 2 * r, y1, x2, y1 + 2 * r, start=0, extent=90, style="pieslice", fill=fill, outline="", tags=tags))
        ids.append(canvas.create_arc(x1, y2 - 2 * r, x1 + 2 * r, y2, start=180, extent=90, style="pieslice", fill=fill, outline="", tags=tags))
        ids.append(canvas.create_arc(x2 - 2 * r, y2 - 2 * r, x2, y2, start=270, extent=90, style="pieslice", fill=fill, outline="", tags=tags))
        if outline and width > 0:
            ids.append(canvas.create_arc(x1, y1, x1 + 2 * r, y1 + 2 * r, start=90, extent=90, style="arc", outline=outline, width=width, tags=tags))
            ids.append(canvas.create_arc(x2 - 2 * r, y1, x2, y1 + 2 * r, start=0, extent=90, style="arc", outline=outline, width=width, tags=tags))
            ids.append(canvas.create_arc(x1, y2 - 2 * r, x1 + 2 * r, y2, start=180, extent=90, style="arc", outline=outline, width=width, tags=tags))
            ids.append(canvas.create_arc(x2 - 2 * r, y2 - 2 * r, x2, y2, start=270, extent=90, style="arc", outline=outline, width=width, tags=tags))
            ids.append(canvas.create_line(x1 + r, y1, x2 - r, y1, fill=outline, width=width, tags=tags))
            ids.append(canvas.create_line(x1 + r, y2, x2 - r, y2, fill=outline, width=width, tags=tags))
            ids.append(canvas.create_line(x1, y1 + r, x1, y2 - r, fill=outline, width=width, tags=tags))
            ids.append(canvas.create_line(x2, y1 + r, x2, y2 - r, fill=outline, width=width, tags=tags))
        return ids

    def draw_minibar():
        nonlocal mini_bar_window_id
        # Re-draw whole minibar every time state changes (hover, mode label, etc).
        canvas.delete("mini_bar")
        x = mini_bar_pos["x"]
        y = mini_bar_pos["y"]
        width = 860
        height = 55
        x1 = x - width // 2
        y1 = y - height // 2
        x2 = x + width // 2
        y2 = y + height // 2
        mini_bar_bounds["bar"] = (x1, y1, x2, y2)
        mini_bar_bounds["buttons"] = {}

        draw_rounded_rect(
            x1, y1, x2, y2, 10,
            fill="#474C53",
            outline="#757B84",
            width=1,
            tags=("mini_bar",),
        )
        canvas.create_text(x1 + 20, y, text="...", fill="#BBC0C7", font=("Segoe UI", 11, "bold"), anchor="w", tags=("mini_bar",))
        canvas.create_text(x1 + 54, y, text="OCR", fill="#ECEFF3", font=("Segoe UI", 11, "bold"), anchor="w", tags=("mini_bar",))

        btn_w = 206
        btn_h = 30
        btn_gap = 14
        buttons = (
            ("translate", TRANSLATE_MODES[mini_bar_state["translate_mode"]]),
            ("selection", f"Selection: {_selection_label_for_bar()}"),
            ("text_mode", f"Format: {_text_mode_label_for_bar()}"),
        )
        start_x = x2 - (btn_w * len(buttons) + btn_gap * (len(buttons) - 1) + 20)
        for i, (key, label) in enumerate(buttons):
            bx1 = start_x + i * (btn_w + btn_gap)
            by1 = y - btn_h // 2
            bx2 = bx1 + btn_w
            by2 = by1 + btn_h
            mini_bar_bounds["buttons"][key] = (bx1, by1, bx2, by2)
            is_hover = mini_bar_hover["button"] == key
            draw_rounded_rect(
                bx1, by1, bx2, by2, 8,
                fill="#7A838D" if is_hover else "#666D76",
                outline="#A8B0B8" if is_hover else "#828991",
                width=1,
                tags=("mini_bar",),
            )
            canvas.create_text(
                (bx1 + bx2) / 2,
                y,
                text=label,
                fill="#FFFFFF" if is_hover else "#E1E5EA",
                font=("Segoe UI", 10, "bold" if is_hover else "normal"),
                tags=("mini_bar",),
            )
        canvas.create_text(x2 - 20, y, text="...", fill="#BBC0C7", font=("Segoe UI", 11, "bold"), anchor="e", tags=("mini_bar",))
        mini_bar_window_id = 1

    def _point_in_bounds(x, y, bounds):
        if bounds is None:
            return False
        x1, y1, x2, y2 = bounds
        return x1 <= x <= x2 and y1 <= y <= y2

    def _shift_minibar_bounds(dx, dy):
        bar = mini_bar_bounds["bar"]
        if bar is not None:
            x1, y1, x2, y2 = bar
            mini_bar_bounds["bar"] = (x1 + dx, y1 + dy, x2 + dx, y2 + dy)
        for key, bounds in mini_bar_bounds["buttons"].items():
            x1, y1, x2, y2 = bounds
            mini_bar_bounds["buttons"][key] = (x1 + dx, y1 + dy, x2 + dx, y2 + dy)

    def _button_at(x, y):
        for key, bounds in mini_bar_bounds["buttons"].items():
            if _point_in_bounds(x, y, bounds):
                return key
        return None

    def set_translate_mode(mode_key: str):
        if mode_key not in TRANSLATE_MODES:
            return
        mini_bar_state["translate_mode"] = mode_key
        draw_minibar()

    def set_selection_mode(mode_name: str):
        if mode_name not in SELECTION_MODES:
            return
        if selection_mode_combo is not None:
            idx = selection_mode_combo.findText(mode_name)
            if idx >= 0:
                selection_mode_combo.setCurrentIndex(idx)
        draw_minibar()

    def set_text_mode(mode_name: str):
        if mode_name not in TEXT_MODES:
            return
        if text_mode_combo is not None:
            idx = text_mode_combo.findText(mode_name)
            if idx >= 0:
                text_mode_combo.setCurrentIndex(idx)
        draw_minibar()

    def open_translate_menu(event):
        try:
            translate_menu.tk_popup(event.x_root, event.y_root)
        finally:
            translate_menu.grab_release()

    def open_selection_menu(event):
        try:
            selection_menu.tk_popup(event.x_root, event.y_root)
        finally:
            selection_menu.grab_release()

    def open_text_mode_menu(event):
        try:
            text_mode_menu.tk_popup(event.x_root, event.y_root)
        finally:
            text_mode_menu.grab_release()

    translate_menu.add_command(label="Off", command=lambda: set_translate_mode("off"))
    translate_menu.add_command(
        label="Include Original",
        command=lambda: set_translate_mode("include_original"),
    )
    translate_menu.add_command(
        label="Translation Only",
        command=lambda: set_translate_mode("translation_only"),
    )
    selection_menu.add_command(
        label="Native selection",
        command=lambda: set_selection_mode("Native selection"),
    )
    selection_menu.add_command(
        label="Box selection",
        command=lambda: set_selection_mode("Box selection"),
    )
    text_mode_menu.add_command(
        label="Original",
        command=lambda: set_text_mode("Original"),
    )
    text_mode_menu.add_command(
        label="Light Clean",
        command=lambda: set_text_mode("Light Clean"),
    )
    text_mode_menu.add_command(
        label="Deep Clean",
        command=lambda: set_text_mode("Deep Clean"),
    )

    def on_minibar_press(event):
        mini_bar_drag["handled_click"] = True
        mini_bar_drag["start_canvas_x"] = event.x
        mini_bar_drag["start_canvas_y"] = event.y
        mini_bar_drag["start_x"] = event.x_root
        mini_bar_drag["start_y"] = event.y_root
        mini_bar_drag["pressed_button"] = _button_at(event.x, event.y)
        mini_bar_drag["active"] = mini_bar_drag["pressed_button"] is None

    def on_minibar_drag(event):
        if mini_bar_window_id is None or not mini_bar_drag["active"]:
            return
        dx = event.x_root - mini_bar_drag["start_x"]
        dy = event.y_root - mini_bar_drag["start_y"]
        mini_bar_pos["x"] += dx
        mini_bar_pos["y"] += dy
        canvas.move("mini_bar", dx, dy)
        _shift_minibar_bounds(dx, dy)
        mini_bar_drag["start_x"] = event.x_root
        mini_bar_drag["start_y"] = event.y_root

    def on_minibar_release(event):
        moved = (
            abs(event.x - mini_bar_drag["start_canvas_x"]) > 4
            or abs(event.y - mini_bar_drag["start_canvas_y"]) > 4
        )
        pressed_button = mini_bar_drag["pressed_button"]
        mini_bar_drag["active"] = False
        mini_bar_drag["pressed_button"] = None
        if not moved:
            # Click on one of the mini buttons opens its popup menu.
            if pressed_button == "translate":
                open_translate_menu(event)
            elif pressed_button == "selection":
                open_selection_menu(event)
            elif pressed_button == "text_mode":
                open_text_mode_menu(event)
        on_canvas_motion(event)

    def on_canvas_motion(event):
        if mini_bar_window_id is None:
            return
        hovered_button = None
        for key, bounds in mini_bar_bounds["buttons"].items():
            if _point_in_bounds(event.x, event.y, bounds):
                hovered_button = key
                break
        if hovered_button != mini_bar_hover["button"]:
            mini_bar_hover["button"] = hovered_button
            draw_minibar()
        if hovered_button is not None:
            canvas.configure(cursor="hand2")
            return
        if _point_in_bounds(event.x, event.y, mini_bar_bounds["bar"]):
            canvas.configure(cursor="arrow")
            return
        if not mini_bar_drag["active"]:
            canvas.configure(cursor="xterm")

    canvas.tag_bind("mini_bar", "<ButtonPress-1>", on_minibar_press)
    canvas.tag_bind("mini_bar", "<B1-Motion>", on_minibar_drag)
    canvas.tag_bind("mini_bar", "<ButtonRelease-1>", on_minibar_release)
    canvas.bind("<Motion>", on_canvas_motion)

    def show_minibar():
        nonlocal mini_bar_window_id
        if mini_bar_window_id is not None:
            return
        draw_minibar()

    status_text_id = canvas.create_text(
        win_width // 2,
        50,
        text="Scanning...",
        fill="#FFFFFF",
        font=("Arial", 22, "bold"),
        anchor="center",
    )
    canvas.update()

    window_ref = overlay_window
    canvas_ref = canvas
    scanning_state = {"idx": 0}
    scanning_dots = [".", "..", "..."]

    def pulse_status():
        nonlocal status_text_id
        if status_text_id is None or window_ref is None:
            return
        try:
            dot_text = scanning_dots[scanning_state["idx"]]
            canvas_ref.itemconfig(
                status_text_id,
                text=f"Scanning{dot_text}",
                font=("Arial", 22, "bold"),
                fill="#FFFFFF",
            )
            scanning_state["idx"] = (scanning_state["idx"] + 1) % len(scanning_dots)
            window_ref.after(350, pulse_status)
        except Exception:
            return

    def run_ocr():
        # OCR is done in worker thread so overlay remains responsive.
        nonlocal ocr_words, status_text_id
        try:
            provider, mode_name = get_active_provider()
            show_minibar_for_mode["value"] = mode_name != "Offline (Tesseract)"
            ocr_words = provider.get_ocr_data(screenshot, win_width, win_height)
        except Exception as e:
            err = str(e)
            print(f"OCR failed: {err}")

            def show_error():
                if status_text_id:
                    canvas_ref.itemconfig(
                        status_text_id,
                        text=f"OCR failed ({mode_name})",
                        fill="#FF8C8C",
                        font=("Arial", 20, "bold"),
                    )
                if show_minibar_for_mode["value"]:
                    show_minibar()

            if window_ref:
                try:
                    window_ref.after(0, show_error)
                except Exception:
                    pass
            return

        print(f"Found {len(ocr_words)} words")
        if last_capture_start is not None:
            elapsed = time.perf_counter() - float(last_capture_start)
            print(f"OCR processing time: {elapsed:.2f}s")

        def remove_status():
            nonlocal status_text_id
            # Remove "Scanning..." once OCR words are ready.
            if status_text_id:
                try:
                    canvas_ref.delete(status_text_id)
                except Exception:
                    pass
                status_text_id = None
            if show_minibar_for_mode["value"]:
                show_minibar()

        if window_ref:
            try:
                window_ref.after(0, remove_status)
            except Exception:
                pass

    window_ref.after(0, pulse_status)
    threading.Thread(target=run_ocr, daemon=True).start()

    def on_mouse_press(event):
        nonlocal selection_start, selection_end, highlight_rects, selected_words, box_rect_id
        current_tags = canvas.gettags("current")
        if "mini_bar" in current_tags:
            selection_start = None
            selection_end = None
            return
        selection_start = (event.x, event.y)
        selection_end = None
        for rect_id in highlight_rects:
            canvas.delete(rect_id)
        highlight_rects = []
        if box_rect_id is not None:
            canvas.delete(box_rect_id)
            box_rect_id = None
        selected_words = []

    def on_mouse_drag(event):
        nonlocal selection_end, highlight_rects, selected_words, last_drag_time, box_rect_id
        if mini_bar_drag["active"]:
            return
        if selection_start is None:
            return
        now = time.perf_counter()
        if now - last_drag_time < 0.03:
            return
        last_drag_time = now
        selection_end = (event.x, event.y)
        for rect_id in highlight_rects:
            canvas.delete(rect_id)
        highlight_rects = []
        if box_rect_id is not None:
            canvas.delete(box_rect_id)
            box_rect_id = None
        if ocr_words:
            assert selection_start is not None
            selection_mode = get_selection_mode()
            if selection_mode == "Box selection":
                left = min(selection_start[0], selection_end[0])
                right = max(selection_start[0], selection_end[0])
                top = min(selection_start[1], selection_end[1])
                bottom = max(selection_start[1], selection_end[1])
                box_rect_id = canvas.create_rectangle(
                    left,
                    top,
                    right,
                    bottom,
                    outline="#7FB2FF",
                    width=2,
                    fill="#7FB2FF",
                    stipple="gray12",
                )
            selected_words = select_words_by_drag(
                ocr_words,
                selection_start[0],
                selection_start[1],
                selection_end[0],
                selection_end[1],
                selection_mode,
            )
            for word in selected_words:
                rect_id = canvas.create_rectangle(word['left'], word['top'], word['right'], word['bottom'], outline='',
                                                  fill='#7FB2FF', width=0, stipple='gray50')
                highlight_rects.append(rect_id)

    def on_mouse_release(_event):
        nonlocal selection_start, selection_end, selected_words, box_rect_id
        global overlay_window
        if mini_bar_drag["handled_click"]:
            mini_bar_drag["handled_click"] = False
            mini_bar_drag["active"] = False
            return
        if selection_start is None:
            return
        if selection_end is None:
            selection_end = selection_start
        if box_rect_id is not None:
            canvas.delete(box_rect_id)
            box_rect_id = None
        if selected_words:
            # Convert selected OCR words back into readable text.
            extracted_text = reconstruct_text_from_words(selected_words)
            formatted_text = apply_text_mode(extracted_text)
            if formatted_text:
                text_to_store = formatted_text
                translate_mode = mini_bar_state["translate_mode"]
                if show_minibar_for_mode["value"] and translate_mode != "off":
                    try:
                        translated_text = translate_with_gemini(formatted_text)
                        if translate_mode == "include_original":
                            text_to_store = (
                                f"Original:\n{formatted_text}\n\n"
                                f"Translation:\n{translated_text}"
                            )
                        else:
                            text_to_store = translated_text
                    except Exception as e:
                        print(f"Translation failed: {e}")
                entry_id = save_to_db(text_to_store)
                session_id_for_capture = current_session_id
                if (
                    show_minibar_for_mode["value"]
                    and get_source_mode() == "On"
                    and session_id_for_capture is not None
                ):
                    # Source guess appends later in background; main copy flow stays instant.
                    _save_source_note_async(
                        formatted_text,
                        screenshot.copy(),
                        session_id_for_capture,
                        entry_id,
                    )
                try:
                    pyperclip.copy(text_to_store)
                except Exception:
                    pass
                invoke_on_ui(refresh_entries)
        if overlay_window is not None:
            overlay_window.destroy()
            overlay_window = None

    def on_escape(_event):
        global overlay_window
        if overlay_window is not None:
            overlay_window.destroy()
            overlay_window = None

    canvas.bind("<Button-1>", on_mouse_press)
    canvas.bind("<B1-Motion>", on_mouse_drag)
    canvas.bind("<ButtonRelease-1>", on_mouse_release)
    overlay_window.bind("<Escape>", on_escape)
    canvas.focus_set()


def start_selection():
    # Hotkey entry point to open overlay and begin capture flow.
    global last_capture_start, overlay_root
    if system_on:
        last_capture_start = time.perf_counter()
        if overlay_root is not None:
            overlay_root.after(0, create_overlay)
        else:
            create_overlay()


def hotkey_listener():
    # Global hotkey listener thread.
    hotkeys = keyboard.GlobalHotKeys({'<ctrl>+q': start_selection})
    hotkeys.start()


def _start_overlay_loop():
    # Separate tkinter loop used only for overlay windows.
    global overlay_root
    overlay_root = tk.Tk()
    overlay_root.withdraw()
    overlay_root.mainloop()


def toggle_system():
    # Toggle capture ON/OFF state and update home status.
    global system_on
    system_on = not system_on
    if system_on:
        if toggle_btn is not None:
            toggle_btn.setProperty("active", True)
            toggle_btn.style().unpolish(toggle_btn)
            toggle_btn.style().polish(toggle_btn)
        if status_lbl is not None:
            status_lbl.setText("ON")
    else:
        if toggle_btn is not None:
            toggle_btn.setProperty("active", False)
            toggle_btn.style().unpolish(toggle_btn)
            toggle_btn.style().polish(toggle_btn)
        if status_lbl is not None:
            status_lbl.setText("OFF")


def refresh_entries():
    # Load session document text into editor area.
    if entries_doc is None or current_session_id is None:
        return
    session_doc = sessions_db.get(doc_id=current_session_id)
    saved_text = (session_doc or {}).get("document_text")
    text = saved_text if isinstance(saved_text, str) else _compose_session_document(current_session_id)
    entries_doc.setPlainText(text)
    cursor = entries_doc.textCursor()
    cursor.movePosition(QtGui.QTextCursor.End)
    entries_doc.setTextCursor(cursor)
    # Keep search highlights in sync after any document refresh.
    if doc_search_input is not None and len(doc_search_input.text().strip()) >= 3:
        run_document_search()
    else:
        _clear_document_search_state()


def on_save_session_document():
    # Persist manual edits made in session document editor.
    if entries_doc is None or current_session_id is None:
        return
    sessions_db.update(
        {"document_text": entries_doc.toPlainText()},
        doc_ids=[current_session_id],
    )


def _clear_document_search_state(message: str = "Type 3+ chars"):
    # Reset search matches and any current highlights.
    global doc_search_matches, doc_search_index
    doc_search_matches = []
    doc_search_index = -1
    if entries_doc is not None:
        entries_doc.setExtraSelections([])
    if doc_search_status_lbl is not None:
        doc_search_status_lbl.setText(message)


def _render_document_search_highlights():
    # Highlight all matches and focus active match.
    if entries_doc is None:
        return
    extras = []
    normal_fmt = QtGui.QTextCharFormat()
    normal_fmt.setBackground(QtGui.QColor(255, 230, 128, 170))
    active_fmt = QtGui.QTextCharFormat()
    active_fmt.setBackground(QtGui.QColor(255, 175, 64, 210))
    for idx, (start, end) in enumerate(doc_search_matches):
        sel = QtWidgets.QTextEdit.ExtraSelection()
        cursor = entries_doc.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QtGui.QTextCursor.KeepAnchor)
        sel.cursor = cursor
        sel.format = active_fmt if idx == doc_search_index else normal_fmt
        extras.append(sel)
    entries_doc.setExtraSelections(extras)
    if doc_search_status_lbl is not None:
        if not doc_search_matches:
            doc_search_status_lbl.setText("No matches")
        else:
            doc_search_status_lbl.setText(f"{doc_search_index + 1}/{len(doc_search_matches)}")
    if doc_search_matches and 0 <= doc_search_index < len(doc_search_matches):
        start, end = doc_search_matches[doc_search_index]
        cursor = entries_doc.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QtGui.QTextCursor.KeepAnchor)
        entries_doc.setTextCursor(cursor)
        entries_doc.ensureCursorVisible()


def run_document_search():
    # Run find-in-document when user types 3+ chars.
    global doc_search_matches, doc_search_index
    if entries_doc is None or doc_search_input is None:
        return
    term = doc_search_input.text().strip()
    if len(term) < 3:
        _clear_document_search_state("Type 3+ chars")
        return
    text = entries_doc.toPlainText()
    matches = list(re.finditer(re.escape(term), text, flags=re.IGNORECASE))
    doc_search_matches = [(m.start(), m.end()) for m in matches]
    doc_search_index = 0 if doc_search_matches else -1
    _render_document_search_highlights()


def jump_document_search(step: int):
    # Move to previous/next search result.
    global doc_search_index
    if not doc_search_matches:
        return
    doc_search_index = (doc_search_index + step) % len(doc_search_matches)
    _render_document_search_highlights()


def on_mode_change(_event=None):
    # Show/hide online-only settings when OCR mode changes.
    if status_lbl is None:
        return
    _provider, mode_name = get_active_provider()
    is_offline = mode_name == "Offline (Tesseract)"
    if text_mode_frame is not None:
        text_mode_frame.setVisible(not is_offline)
    if selection_frame is not None:
        selection_frame.setVisible(not is_offline)
    if source_mode_frame is not None:
        source_mode_frame.setVisible(not is_offline)
    if selection_mode_combo is not None:
        if is_offline:
            idx = selection_mode_combo.findText(DEFAULT_SELECTION_MODE)
            if idx >= 0:
                selection_mode_combo.setCurrentIndex(idx)
            selection_mode_combo.setEnabled(False)
        else:
            selection_mode_combo.setEnabled(True)
    if source_mode_combo is not None and is_offline:
        idx = source_mode_combo.findText(DEFAULT_SOURCE_MODE)
        if idx >= 0:
            source_mode_combo.setCurrentIndex(idx)
    if system_on:
        status_lbl.setText("ON")
    else:
        status_lbl.setText("OFF")


def on_new_session():
    # Create and select new session.
    global current_session_id
    new_id = create_session()
    current_session_id = new_id
    refresh_sessions_list(select_id=new_id)
    if session_title_input is not None:
        session_title_input.setText(sessions_db.get(doc_id=new_id).get("title", ""))
    refresh_entries()


def on_save_session_title():
    # Save edited session title.
    if session_title_input is None or current_session_id is None:
        return
    title = session_title_input.text().strip() or "Untitled Session"
    sessions_db.update({"title": title}, doc_ids=[current_session_id])
    refresh_sessions_list(select_id=current_session_id)
    on_session_select()


def build_gui():
    # Build main PySide window and all tabs/widgets.
    global window, qt_app, toggle_btn, status_lbl
    global ocr_mode_combo, selection_mode_combo, text_mode_combo, source_mode_combo
    global selection_frame, text_mode_frame, source_mode_frame
    global sessions_tree, entries_doc, session_title_input, current_session_id, ui_invoker
    global doc_search_input, doc_search_status_lbl

    qt_app = QtWidgets.QApplication.instance()
    if qt_app is None:
        qt_app = QtWidgets.QApplication([])
    ui_invoker = UiInvoker()

    migrate_legacy_entries()
    if not sessions_db.all():
        current_session_id = create_session()
    if current_session_id is None:
        current_session_id = get_or_create_default_session()

    window = QtWidgets.QMainWindow()
    window.setWindowTitle("OCR Capture")
    window.resize(1140, 720)
    window.setMinimumSize(980, 620)

    central = QtWidgets.QWidget()
    central.setObjectName("appRoot")
    window.setCentralWidget(central)
    root_layout = QtWidgets.QVBoxLayout(central)
    root_layout.setContentsMargins(16, 16, 16, 16)
    root_layout.setSpacing(10)

    tabs = QtWidgets.QTabWidget()
    tabs.setTabPosition(QtWidgets.QTabWidget.North)
    tabs.setMovable(False)
    tabs.setDocumentMode(True)
    root_layout.addWidget(tabs)

    start_page = QtWidgets.QWidget()
    start_layout = QtWidgets.QVBoxLayout(start_page)
    start_layout.setContentsMargins(40, 20, 40, 20)
    start_layout.addStretch(1)
    toggle_btn = QtWidgets.QPushButton("⏻")
    toggle_btn.setObjectName("powerButton")
    toggle_btn.setProperty("active", False)
    toggle_btn.setFixedSize(260, 260)
    toggle_btn.clicked.connect(toggle_system)
    start_layout.addWidget(toggle_btn, alignment=QtCore.Qt.AlignCenter)
    status_lbl = QtWidgets.QLabel("OFF")
    status_lbl.setAlignment(QtCore.Qt.AlignCenter)
    status_lbl.setStyleSheet("font-size: 18px; font-weight: 600;")
    start_layout.addWidget(status_lbl)
    hotkey_hint = QtWidgets.QLabel("Press CTRL+Q anywhere to capture text")
    hotkey_hint.setAlignment(QtCore.Qt.AlignCenter)
    hotkey_hint.setStyleSheet("font-size: 11px; opacity: 0.75;")
    start_layout.addWidget(hotkey_hint)
    start_layout.addStretch(2)
    tabs.addTab(start_page, "Home")

    settings_page = QtWidgets.QWidget()
    settings_layout = QtWidgets.QVBoxLayout(settings_page)
    settings_layout.setContentsMargins(10, 10, 10, 10)
    settings_layout.setSpacing(10)

    settings_group = QtWidgets.QGroupBox("Settings")
    settings_group_layout = QtWidgets.QVBoxLayout(settings_group)
    settings_group_layout.setSpacing(8)
    mode_row = QtWidgets.QHBoxLayout()
    mode_row.addWidget(QtWidgets.QLabel("OCR Mode:"))
    ocr_mode_combo = QtWidgets.QComboBox()
    ocr_mode_combo.addItems(list(OCR_MODES.keys()))
    ocr_mode_combo.setCurrentText(DEFAULT_OCR_MODE)
    ocr_mode_combo.currentIndexChanged.connect(on_mode_change)
    mode_row.addWidget(ocr_mode_combo, 1)
    settings_group_layout.addLayout(mode_row)

    theme_row = QtWidgets.QHBoxLayout()
    theme_row.addWidget(QtWidgets.QLabel("Theme:"))
    theme_select = QtWidgets.QComboBox()
    theme_select.addItems(["Dark", "Light"])
    theme_select.setCurrentText("Dark")
    theme_select.currentTextChanged.connect(apply_theme)
    theme_row.addWidget(theme_select, 1)
    settings_group_layout.addLayout(theme_row)

    text_mode_frame = QtWidgets.QWidget()
    text_mode_layout = QtWidgets.QHBoxLayout(text_mode_frame)
    text_mode_layout.setContentsMargins(0, 0, 0, 0)
    text_mode_layout.addWidget(QtWidgets.QLabel("Text Mode:"))
    text_mode_combo = QtWidgets.QComboBox()
    text_mode_combo.addItems(TEXT_MODES)
    text_mode_combo.setCurrentText(DEFAULT_TEXT_MODE)
    text_mode_layout.addWidget(text_mode_combo, 1)
    settings_group_layout.addWidget(text_mode_frame)

    source_mode_frame = QtWidgets.QWidget()
    source_mode_layout = QtWidgets.QHBoxLayout(source_mode_frame)
    source_mode_layout.setContentsMargins(0, 0, 0, 0)
    source_mode_layout.addWidget(QtWidgets.QLabel("Sources:"))
    source_mode_combo = QtWidgets.QComboBox()
    source_mode_combo.addItems(SOURCE_MODES)
    source_mode_combo.setCurrentText(DEFAULT_SOURCE_MODE)
    source_mode_layout.addWidget(source_mode_combo, 1)
    settings_group_layout.addWidget(source_mode_frame)

    selection_frame = QtWidgets.QWidget()
    selection_layout = QtWidgets.QHBoxLayout(selection_frame)
    selection_layout.setContentsMargins(0, 0, 0, 0)
    selection_layout.addWidget(QtWidgets.QLabel("Selection:"))
    selection_mode_combo = QtWidgets.QComboBox()
    selection_mode_combo.addItems(SELECTION_MODES)
    selection_mode_combo.setCurrentText(DEFAULT_SELECTION_MODE)
    selection_layout.addWidget(selection_mode_combo, 1)
    settings_group_layout.addWidget(selection_frame)
    settings_layout.addWidget(settings_group)

    help_frame = QtWidgets.QGroupBox("Quick Help")
    help_layout = QtWidgets.QVBoxLayout(help_frame)
    tip = (
        "How to Use OCR Capture\n\n"
        "1) Tabs\n"
        "- Home: power button + status.\n"
        "- Settings: OCR/format/source/theme options and this help panel.\n"
        "- Sessions: session list and editable session document.\n\n"
        "2) Start / Stop Capture\n"
        "- Click the Home power button to turn capture ON/OFF.\n"
        "- Press CTRL+Q anywhere to open the frozen overlay.\n"
        "- Drag to select text and release to copy/save.\n\n"
        "3) OCR Mode\n"
        "- Online (Cloud Vision): best recognition, supports text mode and source guess.\n"
        "- Offline (Tesseract): local OCR only.\n"
        "- In offline mode, Text Mode / Selection / Sources controls are hidden.\n\n"
        "4) Selection & Text Modes (Online)\n"
        "- Selection: Native or Box selection behavior in overlay.\n"
        "- Text Mode Original: keep OCR output as-is.\n"
        "- Light Clean: basic spacing cleanup.\n"
        "- Deep Clean: Gemini cleanup, with local fallback if unavailable.\n\n"
        "5) Translation & Source Guess (Overlay / Online)\n"
        "- Mini bar translation: Off, Include Original, Translation Only.\n"
        "- Sources setting (On): app tries to append a source guess for each capture.\n"
        "- Source guess runs in background, so copy/save stays fast.\n\n"
        "6) Sessions & Document Tools\n"
        "- Use New Session to separate notes.\n"
        "- Rename with Session title + Save Title.\n"
        "- Session Document is editable; use Save Document to persist edits.\n"
        "- Find supports 3+ chars, highlights matches, and ▲/▼ navigation."
    )
    tip_box = QtWidgets.QPlainTextEdit()
    tip_box.setReadOnly(True)
    tip_box.setPlainText(tip)
    tip_box.setMinimumHeight(300)
    help_layout.addWidget(tip_box)
    settings_layout.addWidget(help_frame, 1)
    tabs.addTab(settings_page, "Settings")

    sessions_page = QtWidgets.QWidget()
    sessions_layout = QtWidgets.QVBoxLayout(sessions_page)
    sessions_layout.setContentsMargins(10, 10, 10, 10)
    sessions_layout.setSpacing(10)

    sessions_group = QtWidgets.QGroupBox("Sessions")
    sessions_group_layout = QtWidgets.QVBoxLayout(sessions_group)
    sessions_tree = QtWidgets.QTreeWidget()
    sessions_tree.setColumnCount(2)
    sessions_tree.setHeaderLabels(["Title", "Created"])
    sessions_tree.setRootIsDecorated(False)
    sessions_tree.setUniformRowHeights(True)
    sessions_tree.setAlternatingRowColors(True)
    # Keep the list compact while ensuring at least two rows are visible.
    sessions_tree.setMinimumHeight(96)
    sessions_tree.setMaximumHeight(120)
    sessions_tree.itemSelectionChanged.connect(on_session_select)
    sessions_group_layout.addWidget(sessions_tree, 1)
    sessions_group_layout.addWidget(QtWidgets.QLabel("Session title"))
    session_title_input = QtWidgets.QLineEdit()
    sessions_group_layout.addWidget(session_title_input)
    session_actions = QtWidgets.QHBoxLayout()
    new_session_btn = QtWidgets.QPushButton("New Session")
    new_session_btn.clicked.connect(on_new_session)
    save_title_btn = QtWidgets.QPushButton("Save Title")
    save_title_btn.clicked.connect(on_save_session_title)
    session_actions.addWidget(new_session_btn)
    session_actions.addWidget(save_title_btn)
    session_actions.addStretch(1)
    sessions_group_layout.addLayout(session_actions)
    sessions_layout.addWidget(sessions_group, 0)

    entries_frame = QtWidgets.QGroupBox("Session Document")
    entries_layout = QtWidgets.QVBoxLayout(entries_frame)
    entries_doc = QtWidgets.QTextEdit()
    entries_doc.setReadOnly(False)
    entries_layout.addWidget(entries_doc, 1)
    search_layout = QtWidgets.QHBoxLayout()
    search_layout.addWidget(QtWidgets.QLabel("Find:"))
    doc_search_input = QtWidgets.QLineEdit()
    doc_search_input.setPlaceholderText("Min 3 chars")
    doc_search_input.textChanged.connect(lambda _t: run_document_search())
    search_layout.addWidget(doc_search_input, 1)
    search_prev_btn = QtWidgets.QPushButton("▲")
    search_prev_btn.clicked.connect(lambda: jump_document_search(-1))
    search_layout.addWidget(search_prev_btn)
    search_next_btn = QtWidgets.QPushButton("▼")
    search_next_btn.clicked.connect(lambda: jump_document_search(1))
    search_layout.addWidget(search_next_btn)
    doc_search_status_lbl = QtWidgets.QLabel("Type 3+ chars")
    search_layout.addWidget(doc_search_status_lbl)
    entries_layout.addLayout(search_layout)
    actions_layout = QtWidgets.QHBoxLayout()
    refresh_btn = QtWidgets.QPushButton("Refresh")
    refresh_btn.clicked.connect(refresh_entries)
    actions_layout.addWidget(refresh_btn)
    save_doc_btn = QtWidgets.QPushButton("Save Document")
    save_doc_btn.clicked.connect(on_save_session_document)
    actions_layout.addWidget(save_doc_btn)
    actions_layout.addStretch(1)
    entries_layout.addLayout(actions_layout)
    # Document editor takes the remaining vertical space in Sessions tab.
    sessions_layout.addWidget(entries_frame, 1)
    tabs.addTab(sessions_page, "Sessions")

    apply_theme("Dark")
    refresh_sessions_list(select_id=current_session_id)
    on_mode_change()
    refresh_entries()
    window.show()
    qt_app.exec()


def main():
    # Start overlay/hotkey background threads, then launch main UI.
    threading.Thread(target=_start_overlay_loop, daemon=True).start()
    threading.Thread(target=hotkey_listener, daemon=True).start()
    build_gui()


if __name__ == "__main__":
    main()