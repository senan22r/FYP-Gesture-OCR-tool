import ctypes
import datetime
import json
import os
import re
import threading
import time
import tkinter as tk
from tkinter import ttk
import urllib.error
import urllib.request
import ttkbootstrap as tb
from typing import Optional, Tuple
from PIL import ImageGrab, ImageTk
from tinydb import TinyDB, Query
import pyperclip
from pynput import keyboard

import offline
import online

system_on = False
overlay_window: Optional[tk.Toplevel] = None
last_capture_start: Optional[float] = None
window: Optional[tk.Tk] = None
toggle_btn: Optional[ttk.Button] = None
status_lbl: Optional[ttk.Label] = None
history_tree: Optional[ttk.Treeview] = None
history_full_text: Optional[tk.Text] = None
ocr_mode_var: Optional[tk.StringVar] = None
selection_mode_var: Optional[tk.StringVar] = None
text_mode_var: Optional[tk.StringVar] = None
text_mode_frame: Optional[ttk.Frame] = None
selection_frame: Optional[ttk.Frame] = None
selection_select_widget: Optional[ttk.Combobox] = None
sessions_tree: Optional[ttk.Treeview] = None
entries_tree: Optional[ttk.Treeview] = None
entry_full_text: Optional[tk.Text] = None
entries_doc: Optional[tk.Text] = None
session_title_var: Optional[tk.StringVar] = None
current_session_id: Optional[int] = None
DB_PATH = "OCR_DataBase.json"
db = TinyDB(DB_PATH)
sessions_db = db.table("sessions")

OCR_MODES = {
    "Online (Cloud Vision)": online,
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
    if ocr_mode_var is None:
        return OCR_MODES[DEFAULT_OCR_MODE], DEFAULT_OCR_MODE
    name = ocr_mode_var.get() or DEFAULT_OCR_MODE
    return OCR_MODES.get(name, OCR_MODES[DEFAULT_OCR_MODE]), name


def get_selection_mode():
    if selection_mode_var is None:
        return DEFAULT_SELECTION_MODE
    return selection_mode_var.get() or DEFAULT_SELECTION_MODE


def get_text_mode():
    if text_mode_var is None:
        return DEFAULT_TEXT_MODE
    return text_mode_var.get() or DEFAULT_TEXT_MODE


def _light_clean_text(text: str) -> str:
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
    mode = get_text_mode()
    if mode == "Light Clean":
        return _light_clean_text(text)
    if mode == "Deep Clean":
        try:
            return deep_clean_with_gemini(text)
        except Exception:
            return _deep_clean_text(text)
    return text.strip()


def _load_gemini_api_key() -> str:
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


def _call_gemini_prompt(prompt: str) -> str:
    api_key = _load_gemini_api_key()
    if not api_key:
        raise RuntimeError("Missing Gemini API key.")
    payload = {
        "contents": [
            {
                "parts": [{"text": prompt}],
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


def translate_with_gemini(text: str) -> str:
    if not text.strip():
        return ""
    prompt = (
        "Translate the following text to natural English. "
        "Return only the translated text.\n\n"
        f"{text}"
    )
    return _call_gemini_prompt(prompt)


def deep_clean_with_gemini(text: str) -> str:
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


def get_or_create_default_session() -> int:
    existing = sessions_db.all()
    if existing:
        return existing[0].doc_id
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return sessions_db.insert({"title": "Session 1", "created_at": now})


def ensure_legacy_session():
    legacy = sessions_db.get(Query().title == "Legacy")
    if legacy:
        return legacy.doc_id
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return sessions_db.insert({"title": "Legacy", "created_at": now})


def migrate_legacy_entries():
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
    existing = sessions_db.all()
    next_index = len(existing) + 1
    session_title = title or f"Session {next_index}"
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return sessions_db.insert({"title": session_title, "created_at": now})


def refresh_sessions_list(select_id: Optional[int] = None):
    if sessions_tree is None:
        return
    for i in sessions_tree.get_children():
        sessions_tree.delete(i)
    sessions = sessions_db.all()
    sessions_sorted = sorted(sessions, key=lambda r: r.get("created_at", ""))
    for doc in sessions_sorted:
        sessions_tree.insert(
            "",
            "end",
            values=(doc.get("title", ""), doc.get("created_at", ""), doc.doc_id),
        )
    if select_id is not None:
        for item in sessions_tree.get_children():
            if int(sessions_tree.set(item, "session_id")) == select_id:
                sessions_tree.selection_set(item)
                sessions_tree.see(item)
                break


def on_session_select(_event=None):
    global current_session_id
    if sessions_tree is None:
        return
    sel = sessions_tree.selection()
    if not sel:
        return
    item_id = sel[0]
    current_session_id = int(sessions_tree.set(item_id, "session_id"))
    if session_title_var is not None:
        session_title_var.set(sessions_tree.set(item_id, "title"))
    refresh_entries()

def save_to_db(text: str):
    if not text.strip():
        return
    global current_session_id
    if current_session_id is None:
        current_session_id = get_or_create_default_session()
    db.insert({
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "extracted_text": text,
        "session_id": current_session_id,
    })




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


def build_block_bounds(words):
    bounds = {}
    for word in words:
        block = word["block_num"]
        if block not in bounds:
            bounds[block] = [word["left"], word["top"], word["right"], word["bottom"]]
            continue
        left, top, right, bottom = bounds[block]
        bounds[block] = [
            min(left, word["left"]),
            min(top, word["top"]),
            max(right, word["right"]),
            max(bottom, word["bottom"]),
        ]
    return bounds


def find_block_for_point(words, x, y):
    bounds = build_block_bounds(words)
    containing = []
    for block, (left, top, right, bottom) in bounds.items():
        if left <= x <= right and top <= y <= bottom:
            area = max(1, (right - left) * (bottom - top))
            containing.append((area, block))
    if containing:
        containing.sort(key=lambda item: item[0])
        return containing[0][1]
    closest = None
    for block, (left, top, right, bottom) in bounds.items():
        cx = (left + right) / 2
        cy = (top + bottom) / 2
        dist = (cx - x) ** 2 + (cy - y) ** 2
        if closest is None or dist < closest[0]:
            closest = (dist, block)
    return closest[1] if closest else None


def find_word_for_point(words, x, y):
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


def select_words_by_drag(words, start_x, start_y, end_x, end_y, mode, provider_name):
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
    """Show frozen screen overlay"""
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

    border_colors = ["#3B0A0A", "#7A1F1F", "#C23A3A", "#FF9A9A"]
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
        if selection_mode_var is not None:
            selection_mode_var.set(mode_name)
        draw_minibar()

    def set_text_mode(mode_name: str):
        if mode_name not in TEXT_MODES:
            return
        if text_mode_var is not None:
            text_mode_var.set(mode_name)
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
        fill="#FFD36E",
        font=("Arial", 22, "bold"),
        anchor="center",
    )
    canvas.update()

    window_ref = overlay_window
    canvas_ref = canvas
    pulse_state = {"big": False}

    def pulse_status():
        nonlocal status_text_id
        if status_text_id is None or window_ref is None:
            return
        try:
            size = 26 if pulse_state["big"] else 22
            color = "#FFD36E" if pulse_state["big"] else "#FFC046"
            canvas_ref.itemconfig(status_text_id, font=("Arial", size, "bold"), fill=color)
            pulse_state["big"] = not pulse_state["big"]
            window_ref.after(300, pulse_status)
        except Exception:
            return

    def run_ocr():
        nonlocal ocr_words, status_text_id
        ocr_start = time.perf_counter()
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
            provider, provider_name = get_active_provider()
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
                provider_name,
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
                save_to_db(text_to_store)
                try:
                    pyperclip.copy(text_to_store)
                except Exception:
                    pass
                if window is not None:
                    window.after(0, refresh_entries)
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
    global last_capture_start
    if system_on:
        last_capture_start = time.perf_counter()
        if window is not None:
            window.after(0, create_overlay)
        else:
            create_overlay()
    else:
        pass


def hotkey_listener():
    hotkeys = keyboard.GlobalHotKeys({'<ctrl>+q': start_selection})
    hotkeys.start()


def toggle_system():
    global system_on
    system_on = not system_on
    if system_on:
        _provider, mode_name = get_active_provider()
        if toggle_btn is not None:
            toggle_btn.config(text="OCR: ON ✅", bootstyle="success")
        if status_lbl is not None:
            status_lbl.config(
                text=f"ON — Press CTRL+Q to capture text ({mode_name})",
                bootstyle="success",
            )
    else:
        if toggle_btn is not None:
            toggle_btn.config(text="OCR: OFF ❌", bootstyle="secondary")
        if status_lbl is not None:
            status_lbl.config(text="OFF", bootstyle="secondary")


def refresh_entries():
    if entries_doc is None or current_session_id is None:
        return
    rows = db.search(Query().session_id == current_session_id)
    rows_sorted = sorted(rows, key=lambda r: r.get("timestamp", ""))
    entries_doc.delete("1.0", "end")
    parts = []
    for doc in rows_sorted:
        ts = doc.get("timestamp", "")
        full_text = doc.get("extracted_text", "")
        if ts:
            parts.append(f"[{ts}]\n{full_text}".strip())
        else:
            parts.append(full_text.strip())
    entries_doc.insert("1.0", "\n\n\n".join(p for p in parts if p))
    entries_doc.see("end")


def on_mode_change(_event=None):
    global selection_select_widget, text_mode_frame
    if status_lbl is None:
        return
    _provider, mode_name = get_active_provider()
    is_offline = mode_name == "Offline (Tesseract)"
    if text_mode_frame is not None:
        if is_offline:
            if text_mode_frame.winfo_ismapped():
                text_mode_frame.pack_forget()
        else:
            if not text_mode_frame.winfo_ismapped():
                text_mode_frame.pack(side="left", padx=(12, 4))
    if selection_frame is not None:
        if is_offline:
            if selection_mode_var is not None:
                selection_mode_var.set(DEFAULT_SELECTION_MODE)
            if selection_select_widget is not None:
                selection_select_widget.configure(state="disabled")
        else:
            if selection_select_widget is not None:
                selection_select_widget.configure(state="readonly")
        if not selection_frame.winfo_ismapped():
            selection_frame.pack(side="left", padx=(12, 0))
    if system_on:
        status_lbl.config(
            text=f"ON — Press CTRL+Q to capture text ({mode_name})",
            bootstyle="success",
        )
    else:
        status_lbl.config(text="OFF", bootstyle="secondary")


def on_new_session():
    global current_session_id
    new_id = create_session()
    current_session_id = new_id
    refresh_sessions_list(select_id=new_id)
    if session_title_var is not None:
        session_title_var.set(sessions_db.get(doc_id=new_id).get("title", ""))
    refresh_entries()


def on_save_session_title():
    if session_title_var is None or current_session_id is None:
        return
    title = session_title_var.get().strip() or "Untitled Session"
    sessions_db.update({"title": title}, doc_ids=[current_session_id])
    refresh_sessions_list(select_id=current_session_id)
    on_session_select()


def build_gui():
    global window, toggle_btn, status_lbl
    global history_tree, history_full_text, ocr_mode_var, selection_mode_var, selection_frame, text_mode_var
    global selection_select_widget, text_mode_frame
    global sessions_tree, entries_tree, entry_full_text, entries_doc, session_title_var, current_session_id
    window = tb.Window(themename="sandstone")
    window.title("OCR Capture")
    window.geometry("1140x720")
    window.minsize(980, 620)
    app_bg = "#e9edf2"
    panel_bg = "#f0f2f5"
    text_bg = "#f4f5f7"
    window.configure(background=app_bg)
    style = tb.Style()
    style.configure("Treeview", rowheight=30, font=("Segoe UI", 10))
    style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
    style.configure("TButton", padding=(10, 6))
    style.configure("App.TFrame", background=app_bg)
    style.configure("Panel.TFrame", background=panel_bg)
    style.configure("App.TLabel", background=app_bg)
    style.configure("Panel.TLabel", background=panel_bg)
    style.configure("Card.TLabelframe", padding=10, background=panel_bg)
    style.configure("Card.TLabelframe.Label", font=("Segoe UI", 10, "bold"), background=panel_bg)

    migrate_legacy_entries()
    if not sessions_db.all():
        current_session_id = create_session()
    if current_session_id is None:
        current_session_id = get_or_create_default_session()

    root = ttk.Frame(window, padding=16, style="App.TFrame")
    root.pack(fill="both", expand=True)
    paned = ttk.PanedWindow(root, orient="horizontal")
    paned.pack(fill="both", expand=True)

    sidebar = ttk.Frame(paned, width=260, style="Panel.TFrame")
    paned.add(sidebar, weight=1)
    main = ttk.Frame(paned, style="Panel.TFrame")
    paned.add(main, weight=4)

    ttk.Label(sidebar, text="Sessions", font=("Segoe UI", 12, "bold"), style="Panel.TLabel").pack(anchor="w", pady=(0, 8))
    sessions_table = ttk.Frame(sidebar, style="Panel.TFrame")
    sessions_table.pack(fill="both", expand=True, pady=(0, 6))
    sessions_tree = ttk.Treeview(
        sessions_table,
        columns=("title", "created_at", "session_id"),
        show="headings",
        height=12,
        bootstyle="info",
    )
    sessions_tree.heading("title", text="Title")
    sessions_tree.heading("created_at", text="Created")
    sessions_tree.column("title", width=140, anchor="w")
    sessions_tree.column("created_at", width=90, anchor="w")
    sessions_tree.column("session_id", width=0, stretch=False)
    sessions_tree.bind("<<TreeviewSelect>>", on_session_select)
    sessions_tree.pack(side="left", fill="both", expand=True)
    sessions_scroll = ttk.Scrollbar(sessions_table, orient="vertical", command=sessions_tree.yview)
    sessions_scroll.pack(side="right", fill="y")
    sessions_tree.configure(yscrollcommand=sessions_scroll.set)

    session_title_var = tk.StringVar(value="")
    ttk.Label(sidebar, text="Session title", style="Panel.TLabel").pack(anchor="w", pady=(8, 2))
    title_entry = ttk.Entry(sidebar, textvariable=session_title_var)
    title_entry.pack(fill="x")
    session_actions = ttk.Frame(sidebar, style="Panel.TFrame")
    session_actions.pack(fill="x", pady=(6, 0))
    ttk.Button(session_actions, text="New Session", command=on_new_session, bootstyle="success").pack(side="left")
    ttk.Button(session_actions, text="Save Title", command=on_save_session_title, bootstyle="secondary").pack(
        side="left", padx=6
    )

    refresh_sessions_list(select_id=current_session_id)

    top_bar = ttk.LabelFrame(main, text="Capture Controls", style="Card.TLabelframe")
    top_bar.pack(fill="x", pady=(0, 10))
    toggle_btn = ttk.Button(top_bar, text="OCR: OFF ❌", command=toggle_system, bootstyle="secondary")
    toggle_btn.pack(side="left")
    ttk.Label(top_bar, text="Mode:").pack(side="left", padx=(12, 4))
    ocr_mode_var = tk.StringVar(value=DEFAULT_OCR_MODE)
    mode_select = ttk.Combobox(
        top_bar,
        textvariable=ocr_mode_var,
        values=list(OCR_MODES.keys()),
        state="readonly",
        width=22,
    )
    mode_select.pack(side="left")
    mode_select.bind("<<ComboboxSelected>>", on_mode_change)
    text_mode_frame = ttk.Frame(top_bar, style="Panel.TFrame")
    text_mode_frame.pack(side="left", padx=(12, 4))
    ttk.Label(text_mode_frame, text="Text Mode:").pack(side="left", padx=(0, 4))
    text_mode_var = tk.StringVar(value=DEFAULT_TEXT_MODE)
    text_mode_select = ttk.Combobox(
        text_mode_frame,
        textvariable=text_mode_var,
        values=TEXT_MODES,
        state="readonly",
        width=12,
    )
    text_mode_select.pack(side="left")
    selection_frame = ttk.Frame(top_bar, style="Panel.TFrame")
    selection_frame.pack(side="left", padx=(12, 0))
    ttk.Label(selection_frame, text="Selection:").pack(side="left", padx=(0, 4))
    selection_mode_var = tk.StringVar(value=DEFAULT_SELECTION_MODE)
    selection_select = ttk.Combobox(
        selection_frame,
        textvariable=selection_mode_var,
        values=SELECTION_MODES,
        state="readonly",
        width=22,
    )
    selection_select.pack(side="left")
    selection_select_widget = selection_select
    status_lbl = ttk.Label(top_bar, text="OFF", bootstyle="secondary")
    status_lbl.pack(side="left", padx=12)
    on_mode_change()

    help_frame = ttk.LabelFrame(main, text="Quick Help", style="Card.TLabelframe")
    help_frame.pack(fill="x", pady=(0, 10))
    tip = (
        "How to Use OCR Capture\n\n"
        "1) Start / Stop Capture\n"
        "- Click OCR: ON to enable global capture.\n"
        "- Press CTRL+Q anywhere to launch the capture overlay.\n"
        "- Click OCR: OFF to stop listening for hotkeys.\n\n"
        "2) OCR Mode\n"
        "- Online (Cloud Vision): best recognition quality and supports text cleanup options.\n"
        "- Offline (Tesseract): runs locally, useful when internet is unavailable.\n"
        "- In offline mode, selection is fixed to Native selection.\n\n"
        "3) Selection Mode (Online)\n"
        "- Native selection: drag naturally across detected words.\n"
        "- Box selection: draws a rectangle and selects words inside it.\n\n"
        "4) Text Mode (Online)\n"
        "- Original: keeps OCR output exactly as detected.\n"
        "- Light Clean: fixes spacing and blank lines with minimal change.\n"
        "- Deep Clean: strongest cleanup for readability; uses Gemini when API key is available.\n"
        "  If Gemini is unavailable, the app falls back to local cleanup.\n\n"
        "5) Translation in Overlay Mini Bar\n"
        "- Trans: Off -> keep source text only.\n"
        "- Trans: +Orig -> save both original and translated text.\n"
        "- Trans Only -> save only the translated text.\n\n"
        "6) Sessions & Document Panel\n"
        "- Use New Session to separate notes by topic.\n"
        "- Rename with Session title + Save Title.\n"
        "- Captured text is copied to clipboard and appended to Session Document.\n"
        "- Use Refresh if you need to reload entries manually."
    )
    tip_container = ttk.Frame(help_frame, style="Panel.TFrame")
    tip_container.pack(fill="x")
    tip_box = tk.Text(
        tip_container,
        height=15,
        wrap="word",
        padx=10,
        pady=8,
        font=("Segoe UI", 10),
        relief="flat",
        bg=text_bg,
        fg="#1f2933",
        insertbackground="#1f2933",
    )
    tip_box.insert("1.0", tip)
    tip_box.configure(state="disabled")
    tip_box.pack(side="left", fill="x", expand=True)
    tip_scroll = ttk.Scrollbar(tip_container, orient="vertical", command=tip_box.yview)
    tip_scroll.pack(side="right", fill="y")
    tip_box.configure(yscrollcommand=tip_scroll.set)

    entries_frame = ttk.LabelFrame(main, text="Session Document", style="Card.TLabelframe")
    entries_frame.pack(fill="both", expand=True)
    doc_container = ttk.Frame(entries_frame, style="Panel.TFrame")
    doc_container.pack(fill="both", expand=True, padx=8, pady=(8, 10))
    entries_doc = tk.Text(
        doc_container,
        height=16,
        wrap="word",
        font=("Segoe UI", 11),
        padx=10,
        pady=8,
        relief="flat",
        bg=text_bg,
        fg="#1f2933",
        insertbackground="#1f2933",
    )
    entries_doc.pack(side="left", fill="both", expand=True)
    entries_scroll = ttk.Scrollbar(doc_container, orient="vertical", command=entries_doc.yview)
    entries_scroll.pack(side="right", fill="y")
    entries_doc.configure(yscrollcommand=entries_scroll.set)
    actions = ttk.Frame(entries_frame)
    actions.pack(fill="x", padx=6, pady=(0, 8))
    ttk.Button(actions, text="Refresh", command=refresh_entries, bootstyle="secondary").pack(side="left")
    refresh_entries()
    window.mainloop()


def main():
    threading.Thread(target=hotkey_listener, daemon=True).start()
    build_gui()


if __name__ == "__main__":
    main()
