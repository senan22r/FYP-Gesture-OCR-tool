import ctypes
import datetime
import threading
import time
import tkinter as tk
from tkinter import ttk
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
selection_frame: Optional[ttk.Frame] = None
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
    "Box selection (online only)",
]
DEFAULT_SELECTION_MODE = "Native selection"

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
    if mode == "Box selection (online only)" and provider_name == "Online (Cloud Vision)":
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

        if window_ref:
            try:
                window_ref.after(0, remove_status)
            except Exception:
                pass

    window_ref.after(0, pulse_status)
    threading.Thread(target=run_ocr, daemon=True).start()

    def on_mouse_press(event):
        nonlocal selection_start, selection_end, highlight_rects, selected_words, box_rect_id
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
            if selection_mode == "Box selection (online only)" and provider_name == "Online (Cloud Vision)":
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
        if selection_start is None:
            return
        if selection_end is None:
            selection_end = selection_start
        if box_rect_id is not None:
            canvas.delete(box_rect_id)
            box_rect_id = None
        if selected_words:
            extracted_text = reconstruct_text_from_words(selected_words)
            if extracted_text:
                save_to_db(extracted_text)
                try:
                    pyperclip.copy(extracted_text)
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
    if status_lbl is None or not system_on:
        pass
    _provider, mode_name = get_active_provider()
    if selection_frame is not None:
        if mode_name == "Offline (Tesseract)":
            selection_frame.pack_forget()
        else:
            if not selection_frame.winfo_ismapped():
                selection_frame.pack(side="left", padx=(12, 0))
    status_lbl.config(
        text=f"ON — Press CTRL+Q to capture text ({mode_name})",
        bootstyle="success",
    )


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
    global history_tree, history_full_text, ocr_mode_var, selection_mode_var, selection_frame
    global sessions_tree, entries_tree, entry_full_text, entries_doc, session_title_var, current_session_id
    window = tb.Window(themename="cosmo")
    window.title("OCR Capture")
    window.geometry("1080x680")
    window.minsize(980, 600)
    style = tb.Style()
    style.configure("Treeview", rowheight=28, font=("Segoe UI", 10))
    style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))

    migrate_legacy_entries()
    if not sessions_db.all():
        current_session_id = create_session()
    if current_session_id is None:
        current_session_id = get_or_create_default_session()

    root = ttk.Frame(window, padding=14)
    root.pack(fill="both", expand=True)
    paned = ttk.PanedWindow(root, orient="horizontal")
    paned.pack(fill="both", expand=True)

    sidebar = ttk.Frame(paned, width=260)
    paned.add(sidebar, weight=1)
    main = ttk.Frame(paned)
    paned.add(main, weight=4)

    ttk.Label(sidebar, text="Sessions", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 8))
    sessions_tree = ttk.Treeview(
        sidebar,
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
    sessions_tree.pack(fill="both", expand=True, pady=(0, 6))

    session_title_var = tk.StringVar(value="")
    ttk.Label(sidebar, text="Session title").pack(anchor="w", pady=(8, 2))
    title_entry = ttk.Entry(sidebar, textvariable=session_title_var)
    title_entry.pack(fill="x")
    session_actions = ttk.Frame(sidebar)
    session_actions.pack(fill="x", pady=(6, 0))
    ttk.Button(session_actions, text="New Session", command=on_new_session, bootstyle="success").pack(side="left")
    ttk.Button(session_actions, text="Save Title", command=on_save_session_title, bootstyle="secondary").pack(
        side="left", padx=6
    )

    refresh_sessions_list(select_id=current_session_id)

    top_bar = ttk.Frame(main)
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
    selection_frame = ttk.Frame(top_bar)
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
    status_lbl = ttk.Label(top_bar, text="OFF", bootstyle="secondary")
    status_lbl.pack(side="left", padx=12)
    on_mode_change()

    help_frame = ttk.LabelFrame(main, text="Quick Help")
    help_frame.pack(fill="x", pady=(0, 10))
    tip = (
        "1) Click OCR: ON\n"
        "2) Press CTRL+Q to freeze screen\n"
        "3) Drag to select text\n\n"
        "Selected text is copied to clipboard and saved to the current session.\n"
        "Press ESC to cancel selection."
    )
    tip_box = tk.Text(help_frame, height=6, wrap="word", padx=8, pady=6, font=("Segoe UI", 10))
    tip_box.insert("1.0", tip)
    tip_box.configure(state="disabled")
    tip_box.pack(fill="x")

    entries_frame = ttk.LabelFrame(main, text="Session Document")
    entries_frame.pack(fill="both", expand=True)
    entries_doc = tk.Text(entries_frame, height=16, wrap="word", font=("Segoe UI", 11))
    entries_doc.pack(fill="both", expand=True, padx=8, pady=(8, 10))
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
