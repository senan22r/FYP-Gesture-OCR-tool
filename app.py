import datetime
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
import ttkbootstrap as tb
from typing import Optional, Tuple
from PIL import Image, ImageGrab, ImageTk
import numpy as np
import cv2
import pytesseract
from tinydb import TinyDB
import pyperclip
from pynput import keyboard
import textwrap

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

system_on = False
overlay_window: Optional[tk.Toplevel] = None
last_capture_start: Optional[float] = None
window: Optional[tk.Tk] = None
toggle_btn: Optional[ttk.Button] = None
status_lbl: Optional[ttk.Label] = None
history_tree: Optional[ttk.Treeview] = None
history_full_text: Optional[tk.Text] = None
OCR_UPSCALE_FACTOR = 1.2
OCR_UPSCALE_MIN_DIM = 1200
OCR_DPI = 350
SELECTION_COLUMN_PAD_PX = 120
SELECTION_COLUMN_MAX_WIDTH = 2200
SELECTION_MAX_WORD_GAP_PX = 10
DB_PATH = "OCR_DataBase.json"
db = TinyDB(DB_PATH)

def save_to_db(text: str):
    if not text.strip():
        return
    db.insert({
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "extracted_text": text
    })

def preprocess_image_for_ocr(image):
    """Prepare image for OCR (optimized for small text)."""
    img_array = np.array(image)
    if len(img_array.shape) == 3:
        gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
    else:
        gray = img_array

    height, width = gray.shape
    max_dimension = 4000
    if width > max_dimension or height > max_dimension:
        scale = max_dimension / max(width, height)
        new_width = int(width * scale)
        new_height = int(height * scale)
        gray = cv2.resize(gray, (new_width, new_height), interpolation=cv2.INTER_AREA)

    upscale_factor = OCR_UPSCALE_FACTOR if min(gray.shape) < OCR_UPSCALE_MIN_DIM else 1.0
    if upscale_factor > 1.0:
        gray = cv2.resize(
            gray,
            (int(gray.shape[1] * upscale_factor), int(gray.shape[0] * upscale_factor)),
            interpolation=cv2.INTER_CUBIC
        )

    mean_brightness = np.mean(gray)
    is_dark_background = mean_brightness < 100

    # denoise
    denoised = cv2.fastNlMeansDenoising(gray, None, h=3, templateWindowSize=5, searchWindowSize=15)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    enhanced = clahe.apply(denoised)

    # sharpening
    blurred = cv2.GaussianBlur(enhanced, (0, 0), 1.0)
    sharpened = cv2.addWeighted(enhanced, 1.5, blurred, -0.5, 0)

    # Prefer Otsu for small text, fallback to adaptive on dark background.
    if is_dark_background:
        processed = cv2.adaptiveThreshold(
            sharpened, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 4
        )
    else:
        _, processed = cv2.threshold(sharpened, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    return processed

def extract_text(image):
    processed = preprocess_image_for_ocr(image)
    text = pytesseract.image_to_string(processed, config=f'--psm 6 --oem 1 --dpi {OCR_DPI}')
    return (text or "").strip()

def get_ocr_data(image, display_width, display_height):
    """Get word positions from OCR."""
    img_array = np.array(image)
    if len(img_array.shape) == 3:
        orig_height, orig_width = img_array.shape[:2]
    else:
        orig_height, orig_width = img_array.shape
    
    ocr_scale = 1.0
    max_dimension = 4000
    if orig_width > max_dimension or orig_height > max_dimension:
        ocr_scale = max_dimension / max(orig_width, orig_height)
        new_width = int(orig_width * ocr_scale)
        new_height = int(orig_height * ocr_scale)
        if len(img_array.shape) == 3:
            resized = cv2.resize(img_array, (new_width, new_height), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(resized, cv2.COLOR_RGB2GRAY)
        else:
            gray = cv2.resize(img_array, (new_width, new_height), interpolation=cv2.INTER_AREA)
    else:
        if len(img_array.shape) == 3:
            gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
        else:
            gray = img_array
    
    upscale_factor = OCR_UPSCALE_FACTOR if min(gray.shape) < OCR_UPSCALE_MIN_DIM else 1.0
    if upscale_factor > 1.0:
        gray = cv2.resize(
            gray,
            (int(gray.shape[1] * upscale_factor), int(gray.shape[0] * upscale_factor)),
            interpolation=cv2.INTER_CUBIC
        )

    mean_brightness = np.mean(gray)
    is_dark_background = mean_brightness < 100
    denoised = cv2.fastNlMeansDenoising(gray, None, h=3, templateWindowSize=5, searchWindowSize=15)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    enhanced = clahe.apply(denoised)
    blurred = cv2.GaussianBlur(enhanced, (0, 0), 1.0)
    sharpened = cv2.addWeighted(enhanced, 1.5, blurred, -0.5, 0)
    if is_dark_background:
        processed = cv2.adaptiveThreshold(
            sharpened, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 4
        )
    else:
        _, processed = cv2.threshold(sharpened, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    data = pytesseract.image_to_data(
        processed,
        output_type=pytesseract.Output.DICT,
        config=f'--psm 6 --oem 1 --dpi {OCR_DPI}'
    )
    
    words = []
    n_boxes = len(data['text'])
    
    scale_from_ocr = 1.0 / ocr_scale / upscale_factor
    scale_to_display_x = display_width / orig_width
    scale_to_display_y = display_height / orig_height
    
    for i in range(n_boxes):
        text = data['text'][i].strip()
        conf = int(data['conf'][i])
        if text and conf > 0:
            left = int(data['left'][i] * scale_from_ocr * scale_to_display_x)
            top = int(data['top'][i] * scale_from_ocr * scale_to_display_y)
            width = int(data['width'][i] * scale_from_ocr * scale_to_display_x)
            height = int(data['height'][i] * scale_from_ocr * scale_to_display_y)
            words.append({
                'text': text,
                'left': left,
                'top': top,
                'width': width,
                'height': height,
                'right': left + width,
                'bottom': top + height,
                'conf': conf,
                'block_num': data['block_num'][i],
                'par_num': data['par_num'][i],
                'line_num': data['line_num'][i],
                'word_num': data['word_num'][i]
            })
    return words

def reconstruct_text_from_words(words):
    """Rebuild text using Tesseract's line/word ordering."""
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
    return not (rect1['right'] < rect2['left'] or rect1['left'] > rect2['right'] or rect1['bottom'] < rect2['top'] or rect1['top'] > rect2['bottom'])

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
    for key in line_order:
        top, bottom = line_bounds[key]
        if top <= y <= bottom:
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

def clamp_line_segment(line_words, anchor_x):
    if not line_words:
        return []
    def center_x(word):
        return (word['left'] + word['right']) / 2
    closest_idx = min(range(len(line_words)), key=lambda i: abs(center_x(line_words[i]) - anchor_x))
    left = right = closest_idx
    while left > 0:
        gap = line_words[left]['left'] - line_words[left - 1]['right']
        if gap > SELECTION_MAX_WORD_GAP_PX:
            break
        left -= 1
    while right < len(line_words) - 1:
        gap = line_words[right + 1]['left'] - line_words[right]['right']
        if gap > SELECTION_MAX_WORD_GAP_PX:
            break
        right += 1
    return line_words[left:right + 1]

def select_words_by_drag(words, start_x, start_y, end_x, end_y, line_index=None):
    if not words:
        return []
    if line_index is None:
        line_order, line_map, line_bounds = build_line_index(words)
    else:
        line_order, line_map, line_bounds = line_index
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
        line_words = clamp_line_segment(line_map[key], start_x)
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
    
    overlay_window.update_idletasks()
    win_width = overlay_window.winfo_screenwidth()
    win_height = overlay_window.winfo_screenheight()

    
    if screenshot.width != win_width or screenshot.height != win_height:
        try:
            screenshot = screenshot.resize((win_width, win_height), Image.Resampling.LANCZOS)
        except AttributeError:
            screenshot = screenshot.resize((win_width, win_height), Image.BICUBIC)
    
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
    line_index = None
    highlight_rects = []
    status_text_id = None
    last_drag_time = 0.0
    
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
        nonlocal ocr_words, line_index
        ocr_start = time.perf_counter()
        ocr_words = get_ocr_data(screenshot, win_width, win_height)
        line_index = build_line_index(ocr_words) if ocr_words else None
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
        nonlocal selection_start, selection_end, highlight_rects, selected_words
        selection_start = (event.x, event.y)
        selection_end = None
        for rect_id in highlight_rects:
            canvas.delete(rect_id)
        highlight_rects = []
        selected_words = []
    
    def on_mouse_drag(event):
        nonlocal selection_end, highlight_rects, selected_words, last_drag_time
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
        if ocr_words:
            assert selection_start is not None
            selected_words = select_words_by_drag(
                ocr_words,
                selection_start[0],
                selection_start[1],
                selection_end[0],
                selection_end[1],
                line_index,
            )
            for word in selected_words:
                rect_id = canvas.create_rectangle(word['left'], word['top'], word['right'], word['bottom'], outline='', fill='#7FB2FF', width=0, stipple='gray50')
                highlight_rects.append(rect_id)
    
    def on_mouse_release(_event):
        nonlocal selection_start, selection_end, selected_words
        global overlay_window
        if selection_start is None:
            return
        if selection_end is None:
            selection_end = selection_start
        if selected_words:
            extracted_text = reconstruct_text_from_words(selected_words)
            if extracted_text:
                save_to_db(extracted_text)
                try:
                    pyperclip.copy(extracted_text)
                except Exception:
                    pass
                if window is not None:
                    window.after(0, refresh_history)
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
        if toggle_btn is not None:
            toggle_btn.config(text="OCR: ON ✅", bootstyle="success")
        if status_lbl is not None:
            status_lbl.config(text="ON — Press CTRL+Q to capture text", bootstyle="success")
    else:
        if toggle_btn is not None:
            toggle_btn.config(text="OCR: OFF ❌", bootstyle="secondary")
        if status_lbl is not None:
            status_lbl.config(text="OFF", bootstyle="secondary")

def delete_selected_history():
    if history_tree is None:
        return
    sel = history_tree.selection()
    if not sel:
        messagebox.showinfo("Delete", "Select a row first.")
        return
    item_id = sel[0]
    doc_id = int(history_tree.set(item_id, "doc_id"))
    if messagebox.askyesno("Delete", "Delete this entry?"):
        try:
            db.remove(doc_ids=[doc_id])
            refresh_history()
        except Exception as e:
            messagebox.showerror("Delete", f"Failed to delete:\n{e}")

def refresh_history():
    if history_tree is None:
        return
    for i in history_tree.get_children():
        history_tree.delete(i)
    rows = db.all()
    rows_sorted = sorted(rows, key=lambda r: r.get("timestamp",""), reverse=True)
    for doc in rows_sorted:
        doc_id = getattr(doc, "doc_id", None)
        ts = doc.get("timestamp", "")
        full_text = doc.get("extracted_text", "")
        preview = textwrap.shorten(full_text.replace("\n", " "), width=80, placeholder="…")
        history_tree.insert("", "end", values=(ts, preview, full_text, doc_id))

def on_history_select(_event=None):
    if history_tree is None or history_full_text is None:
        return
    sel = history_tree.selection()
    if not sel:
        history_full_text.delete("1.0", "end")
        return
    item_id = sel[0]
    text = history_tree.set(item_id, "text_full")
    history_full_text.delete("1.0", "end")
    history_full_text.insert("1.0", text)

def build_gui():
    global window, toggle_btn, status_lbl
    global history_tree, history_full_text
    window = tb.Window(themename="flatly")
    window.title("OCR Capture")
    window.geometry("720x520")
    notebook = ttk.Notebook(window, padding=6)
    notebook.pack(fill="both", expand=True, padx=10, pady=10)
    tab_capture = ttk.Frame(notebook)
    notebook.add(tab_capture, text="Capture")
    top_bar = ttk.Frame(tab_capture)
    top_bar.pack(fill="x", pady=(10, 6))
    toggle_btn = ttk.Button(top_bar, text="OCR: OFF ❌", command=toggle_system, bootstyle="secondary")
    toggle_btn.pack(side="left")
    status_lbl = ttk.Label(top_bar, text="OFF", bootstyle="secondary")
    status_lbl.pack(side="left", padx=10)
    tip = (
        "Help:\n"
        "1) Click 'OCR: ON',\n"
        "2) Press CTRL+Q to freeze screen,\n"
        "3) Drag over text to select (like native selection).\n\n"
        "Selected text is copied to clipboard and saved to history.\n"
        "Press ESC to cancel selection."
    )
    tip_box = tk.Text(tab_capture, height=10, wrap="word", padx=8, pady=8)
    tip_box.insert("1.0", tip)
    tip_box.configure(state="disabled")
    tip_box.pack(fill="both", expand=True, pady=(6, 10))
    tab_history = ttk.Frame(notebook)
    notebook.add(tab_history, text="History")
    cols = ("timestamp", "preview", "text_full", "doc_id")
    history_tree = ttk.Treeview(tab_history, columns=cols, show="headings", height=10, bootstyle="info")
    history_tree.heading("timestamp", text="Timestamp")
    history_tree.heading("preview", text="Preview")
    history_tree.column("timestamp", width=160, anchor="w")
    history_tree.column("preview", width=520, anchor="w")
    history_tree.column("text_full", width=0, stretch=False)
    history_tree.column("doc_id", width=0, stretch=False)
    history_tree.bind("<<TreeviewSelect>>", on_history_select)
    history_tree.pack(fill="x", padx=6, pady=(8, 4))
    history_full_text = tk.Text(tab_history, height=10, wrap="word")
    history_full_text.pack(fill="both", expand=True, padx=6, pady=(0, 8))
    actions = ttk.Frame(tab_history)
    actions.pack(fill="x", padx=6, pady=(0, 10))
    ttk.Button(actions, text="Refresh", command=refresh_history, bootstyle="secondary").pack(side="left")
    ttk.Button(actions, text="Delete Selected", command=delete_selected_history, bootstyle="danger").pack(side="left", padx=6)
    refresh_history()
    window.mainloop()

def main():
    threading.Thread(target=hotkey_listener, daemon=True).start()
    build_gui()

if __name__ == "__main__":
    main()