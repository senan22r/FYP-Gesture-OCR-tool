import datetime
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from PIL import ImageGrab
import numpy as np
import cv2
import pytesseract
from tinydb import TinyDB
import pyperclip
from pynput import keyboard, mouse
import textwrap

# ---- Tesseract PATH ----
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# ---- App State ----
system_on = False
selecting = False
start_pos = None
current_pos = None

# ---- DATABASE ----
DB_PATH = "OCR_DataBase.json"
db = TinyDB(DB_PATH)  # uses default table ("_default") like your original

def save_to_db(text: str):
    if not text.strip():
        return
    db.insert({
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "extracted_text": text
    })

# ---- OCR ----
def extract_text(image):
    gray = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2GRAY)
    text = pytesseract.image_to_string(gray)
    return (text or "").strip()

# ---- Mouse via pynput ----
def on_mouse_move(x, y):
    global current_pos
    if selecting and system_on:
        current_pos = (x, y)

def on_mouse_click(x, y, button, pressed):
    global selecting, start_pos, current_pos

    if not system_on:
        return

    if selecting and pressed:
        start_pos = (x, y)
        current_pos = (x, y)

    elif selecting and not pressed:
        selecting = False

        if start_pos and current_pos:
            x1, y1 = start_pos
            x2, y2 = current_pos

            img = ImageGrab.grab(bbox=(min(x1,x2), min(y1,y2), max(x1,x2), max(y1,y2)))
            text = extract_text(img)

            print("\nOUTPUT:")
            print(text if text else "No text found")

            if text:
                save_to_db(text)
                try:
                    pyperclip.copy(text)
                    print("✅ Copied to clipboard")
                except Exception as e:
                    print(f"⚠ Clipboard copy failed: {e}")
                print(f"✅ Saved to {DB_PATH}")
                # update history tab (on UI thread)
                if 'history_tree' in globals():
                    window.after(0, refresh_history)
        else:
            print("⚠ No selection made.")

def start_selection():
    global selecting
    if system_on:
        selecting = True
        print("✅ CTRL+Q pressed — drag to select area…")
    else:
        print("⚠ OCR is OFF — turn it ON first.")

def hotkey_listener():
    hotkeys = keyboard.GlobalHotKeys({
        '<ctrl>+q': start_selection
    })
    hotkeys.start()

def mouse_listener():
    mouse.Listener(on_move=on_mouse_move, on_click=on_mouse_click).start()

# ---- GUI ----
def toggle_system():
    global system_on
    system_on = not system_on

    if system_on:
        toggle_btn.config(text="OCR: ON ✅")
        status_lbl.config(text="ON — Press CTRL+Q and drag to capture")
        print("✅ OCR System Activated")
    else:
        toggle_btn.config(text="OCR: OFF ❌")
        status_lbl.config(text="OFF")
        print("❌ OCR System Deactivated")

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
    # Clear
    for i in history_tree.get_children():
        history_tree.delete(i)

    # Load newest first
    rows = db.all()
    rows_sorted = sorted(rows, key=lambda r: r.get("timestamp",""), reverse=True)

    for doc in rows_sorted:
        doc_id = getattr(doc, "doc_id", None)
        ts = doc.get("timestamp", "")
        full_text = doc.get("extracted_text", "")
        preview = textwrap.shorten(full_text.replace("\n", " "), width=80, placeholder="…")
        history_tree.insert(
            "", "end",
            values=(ts, preview, full_text, doc_id)
        )

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

    # ttk look
    style = ttk.Style(window)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    notebook = ttk.Notebook(window)
    notebook.pack(fill="both", expand=True, padx=10, pady=10)

    # --- TAB: Capture ---
    tab_capture = ttk.Frame(notebook)
    notebook.add(tab_capture, text="Capture")

    top_bar = ttk.Frame(tab_capture)
    top_bar.pack(fill="x", pady=(10, 6))

    toggle_btn = ttk.Button(top_bar, text="OCR: OFF ❌", command=toggle_system)
    toggle_btn.pack(side="left")

    status_lbl = ttk.Label(top_bar, text="OFF")
    status_lbl.pack(side="left", padx=10)

    tip = (
        "Tip:\n"
        "1) Click 'OCR: ON',\n"
        "2) Press CTRL+Q,\n"
        "3) Drag a rectangle over the text to capture.\n\n"
        "Captured text is copied to clipboard and saved to history."
    )
    tip_box = tk.Text(tab_capture, height=10, wrap="word", padx=8, pady=8)
    tip_box.insert("1.0", tip)
    tip_box.configure(state="disabled")
    tip_box.pack(fill="both", expand=True, pady=(6, 10))

    # --- TAB: History ---
    tab_history = ttk.Frame(notebook)
    notebook.add(tab_history, text="History")

    cols = ("timestamp", "preview", "text_full", "doc_id")
    history_tree = ttk.Treeview(tab_history, columns=cols, show="headings", height=10)
    history_tree.heading("timestamp", text="Timestamp")
    history_tree.heading("preview", text="Preview")
    history_tree.column("timestamp", width=160, anchor="w")
    history_tree.column("preview", width=520, anchor="w")
    history_tree.column("text_full", width=0, stretch=False)  # hidden
    history_tree.column("doc_id", width=0, stretch=False)     # hidden
    history_tree.bind("<<TreeviewSelect>>", on_history_select)
    history_tree.pack(fill="x", padx=6, pady=(8, 4))

    history_full_text = tk.Text(tab_history, height=10, wrap="word")
    history_full_text.pack(fill="both", expand=True, padx=6, pady=(0, 8))

    actions = ttk.Frame(tab_history)
    actions.pack(fill="x", padx=6, pady=(0, 10))
    ttk.Button(actions, text="Refresh", command=refresh_history).pack(side="left")
    ttk.Button(actions, text="Delete Selected", command=delete_selected_history).pack(side="left", padx=6)

    # initial history load
    refresh_history()

    window.mainloop()

# ---- MAIN ----
def main():
    print("✅ Program started.")
    print("➡ Turn ON using GUI")
    print("➡ When ON, press CTRL+Q and drag")

    threading.Thread(target=mouse_listener, daemon=True).start()
    threading.Thread(target=hotkey_listener, daemon=True).start()

    build_gui()

if __name__ == "__main__":
    main()

