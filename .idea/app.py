import datetime
import threading
import tkinter as tk
from tkinter import ttk, messagebox
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
overlay_window = None

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
    """Prepare image for OCR"""
    img_array = np.array(image)
    if len(img_array.shape) == 3:
        gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
    else:
        gray = img_array
    
    height, width = gray.shape
    max_dimension = 2000
    if width > max_dimension or height > max_dimension:
        scale = max_dimension / max(width, height)
        new_width = int(width * scale)
        new_height = int(height * scale)
        gray = cv2.resize(gray, (new_width, new_height), interpolation=cv2.INTER_AREA)
    
    mean_brightness = np.mean(gray)
    is_dark_background = mean_brightness < 100
    
    denoised = cv2.fastNlMeansDenoising(gray, None, h=5, templateWindowSize=5, searchWindowSize=15)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    enhanced = clahe.apply(denoised)
    
    if is_dark_background:
        thresh = cv2.adaptiveThreshold(enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 9, 2)
    else:
        thresh = cv2.adaptiveThreshold(enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 9, 2)
    
    return thresh

def extract_text(image):
    processed = preprocess_image_for_ocr(image)
    text = pytesseract.image_to_string(processed, config='--psm 6 --oem 3')
    return (text or "").strip()

def get_ocr_data(image, display_width, display_height):
    """Get word positions from OCR"""
    img_array = np.array(image)
    if len(img_array.shape) == 3:
        orig_height, orig_width = img_array.shape[:2]
    else:
        orig_height, orig_width = img_array.shape
    
    ocr_scale = 1.0
    max_dimension = 2000
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
    
    mean_brightness = np.mean(gray)
    is_dark_background = mean_brightness < 100
    denoised = cv2.fastNlMeansDenoising(gray, None, h=5, templateWindowSize=5, searchWindowSize=15)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    enhanced = clahe.apply(denoised)
    if is_dark_background:
        processed = cv2.adaptiveThreshold(enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 9, 2)
    else:
        processed = cv2.adaptiveThreshold(enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 9, 2)
    
    data = pytesseract.image_to_data(processed, output_type=pytesseract.Output.DICT, config='--psm 6 --oem 3')
    
    words = []
    n_boxes = len(data['text'])
    
    scale_from_ocr = 1.0 / ocr_scale
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
                'conf': conf
            })
    return words

def point_in_rect(x, y, rect):
    """Check if point inside rectangle"""
    return (rect['left'] <= x <= rect['right'] and rect['top'] <= y <= rect['bottom'])

def rects_intersect(rect1, rect2):
    """Check if rectangles overlap"""
    return not (rect1['right'] < rect2['left'] or rect1['left'] > rect2['right'] or rect1['bottom'] < rect2['top'] or rect1['top'] > rect2['bottom'])

def get_selected_words(words, start_x, start_y, end_x, end_y):
    """Find words in selection area"""
    selection_rect = {
        'left': min(start_x, end_x),
        'top': min(start_y, end_y),
        'right': max(start_x, end_x),
        'bottom': max(start_y, end_y)
    }
    selected = []
    for word in words:
        word_rect = {
            'left': word['left'],
            'top': word['top'],
            'right': word['right'],
            'bottom': word['bottom']
        }
        center_x = (word['left'] + word['right']) / 2
        center_y = (word['top'] + word['bottom']) / 2
        if (point_in_rect(center_x, center_y, selection_rect) or rects_intersect(selection_rect, word_rect)):
            selected.append(word)
    selected.sort(key=lambda w: (w['top'], w['left']))
    return selected

def create_overlay():
    """Show frozen screen overlay"""
    global overlay_window
    if overlay_window is not None:
        return
    
    screenshot = ImageGrab.grab()
    screen_width = screenshot.width
    screen_height = screenshot.height
    
    overlay_window = tk.Toplevel()
    overlay_window.attributes('-fullscreen', True)
    overlay_window.attributes('-topmost', True)
    overlay_window.attributes('-alpha', 0.99)
    overlay_window.overrideredirect(True)
    overlay_window.configure(cursor="crosshair")
    
    overlay_window.update_idletasks()
    win_width = overlay_window.winfo_screenwidth()
    win_height = overlay_window.winfo_screenheight()
    
    if screenshot.width != win_width or screenshot.height != win_height:
        try:
            screenshot = screenshot.resize((win_width, win_height), Image.Resampling.LANCZOS)
        except AttributeError:
            screenshot = screenshot.resize((win_width, win_height), Image.LANCZOS)
    
    photo = ImageTk.PhotoImage(screenshot)
    canvas = tk.Canvas(overlay_window, highlightthickness=0, cursor="crosshair", width=win_width, height=win_height)
    canvas.pack(fill="both", expand=True)
    canvas.create_image(0, 0, anchor="nw", image=photo)
    canvas.image = photo
    
    border_width = 2
    canvas.create_rectangle(0, 0, win_width, win_height, outline='red', width=border_width)
    
    selection_start = None
    selection_end = None
    selected_words = []
    ocr_words = []
    highlight_rects = []
    status_text_id = None
    
    status_text_id = canvas.create_text(win_width // 2, 50, text="Processing OCR... Please wait", fill="yellow", font=("Arial", 16, "bold"), anchor="center")
    canvas.update()
    
    window_ref = overlay_window
    canvas_ref = canvas
    
    def run_ocr():
        nonlocal ocr_words
        print("Running OCR on frozen screen...")
        ocr_words = get_ocr_data(screenshot, win_width, win_height)
        print(f"Found {len(ocr_words)} words")
        def remove_status():
            if status_text_id:
                try:
                    canvas_ref.delete(status_text_id)
                except:
                    pass
        if window_ref:
            try:
                window_ref.after(0, remove_status)
            except:
                pass
    
    threading.Thread(target=run_ocr, daemon=True).start()
    
    def on_mouse_press(event):
        nonlocal selection_start, highlight_rects, selected_words
        selection_start = (event.x, event.y)
        selection_end = None
        for rect_id in highlight_rects:
            canvas.delete(rect_id)
        highlight_rects = []
        selected_words = []
    
    def on_mouse_drag(event):
        nonlocal selection_end, highlight_rects, selected_words
        if selection_start is None:
            return
        selection_end = (event.x, event.y)
        for rect_id in highlight_rects:
            canvas.delete(rect_id)
        highlight_rects = []
        if ocr_words:
            selected_words = get_selected_words(ocr_words, selection_start[0], selection_start[1], selection_end[0], selection_end[1])
            for word in selected_words:
                rect_id = canvas.create_rectangle(word['left'], word['top'], word['right'], word['bottom'], outline='blue', fill='lightblue', width=2, stipple='gray50')
                highlight_rects.append(rect_id)
    
    def on_mouse_release(event):
        nonlocal selection_start, selection_end, selected_words
        global overlay_window
        if selection_start is None:
            return
        if selection_end is None:
            selection_end = selection_start
        if selected_words:
            text_lines = []
            current_line = []
            last_y = None
            for word in selected_words:
                if last_y is None or abs(word['top'] - last_y) > word['height'] * 0.5:
                    if current_line:
                        text_lines.append(' '.join(current_line))
                    current_line = [word['text']]
                    last_y = word['top']
                else:
                    current_line.append(word['text'])
                    last_y = word['top']
            if current_line:
                text_lines.append(' '.join(current_line))
            extracted_text = '\n'.join(text_lines).strip()
            if extracted_text:
                print("\nOUTPUT:")
                print(extracted_text)
                save_to_db(extracted_text)
                try:
                    pyperclip.copy(extracted_text)
                    print("Copied to clipboard")
                except Exception as e:
                    print(f"Copy failed: {e}")
                if 'history_tree' in globals():
                    window.after(0, refresh_history)
        if overlay_window:
            overlay_window.destroy()
            overlay_window = None
    
    def on_escape(event):
        global overlay_window
        if overlay_window:
            overlay_window.destroy()
            overlay_window = None
    
    canvas.bind("<Button-1>", on_mouse_press)
    canvas.bind("<B1-Motion>", on_mouse_drag)
    canvas.bind("<ButtonRelease-1>", on_mouse_release)
    overlay_window.bind("<Escape>", on_escape)
    canvas.focus_set()

def start_selection():
    if system_on:
        if 'window' in globals():
            window.after(0, create_overlay)
        else:
            create_overlay()
        print("CTRL+Q pressed — overlay activated")
    else:
        print("OCR is OFF")

def hotkey_listener():
    hotkeys = keyboard.GlobalHotKeys({'<ctrl>+q': start_selection})
    hotkeys.start()

def toggle_system():
    global system_on
    system_on = not system_on
    if system_on:
        toggle_btn.config(text="OCR: ON ✅")
        status_lbl.config(text="ON — Press CTRL+Q to capture text")
        print("Activated")
    else:
        toggle_btn.config(text="OCR: OFF ❌")
        status_lbl.config(text="OFF")
        print("Deactivated")

def delete_selected_history():
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

def on_history_select(event=None):
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
    window = tk.Tk()
    window.title("OCR Capture")
    window.geometry("720x520")
    style = ttk.Style(window)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    notebook = ttk.Notebook(window)
    notebook.pack(fill="both", expand=True, padx=10, pady=10)
    tab_capture = ttk.Frame(notebook)
    notebook.add(tab_capture, text="Capture")
    top_bar = ttk.Frame(tab_capture)
    top_bar.pack(fill="x", pady=(10, 6))
    toggle_btn = ttk.Button(top_bar, text="OCR: OFF ❌", command=toggle_system)
    toggle_btn.pack(side="left")
    status_lbl = ttk.Label(top_bar, text="OFF")
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
    history_tree = ttk.Treeview(tab_history, columns=cols, show="headings", height=10)
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
    ttk.Button(actions, text="Refresh", command=refresh_history).pack(side="left")
    ttk.Button(actions, text="Delete Selected", command=delete_selected_history).pack(side="left", padx=6)
    refresh_history()
    window.mainloop()

def main():
    threading.Thread(target=hotkey_listener, daemon=True).start()
    build_gui()

if __name__ == "__main__":
    main()
