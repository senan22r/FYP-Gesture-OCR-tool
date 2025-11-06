import tkinter as tk
from pynput import mouse, keyboard
from PIL import ImageGrab
import pytesseract
import numpy as np
import cv2
import sqlite3
import datetime

# Path to tesseract
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

selecting = False
start_pos = None
current_pos = None


# ----- Database -----
def db():
    conn = sqlite3.connect("ORC_DataBase.db")
    cur = conn.cursor()
    cur.execute("""
                CREATE TABLE IF NOT EXISTS ocr_history (
                                                           id INTEGER PRIMARY KEY AUTOINCREMENT,
                                                           timestamp TEXT,
                                                           extracted_text TEXT
                )
                """)
    conn.commit()
    conn.close()


def save_to_db(text):
    conn = sqlite3.connect("ORC_DataBase.db")
    cur = conn.cursor()
    cur.execute("""
                INSERT INTO ocr_history (timestamp, extracted_text)
                VALUES (?, ?)
                """, (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), text))
    conn.commit()
    conn.close()


# ----- OCR -----
def extract_text(image):
    gray = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2GRAY)
    text = pytesseract.image_to_string(gray)
    return text.strip()


# ----- GUI Overlay Window -----
root = tk.Tk()
root.attributes("-fullscreen", True)
root.attributes("-alpha", 0.2)  # transparent background
root.config(bg="black")
root.attributes("-topmost", True)
canvas = tk.Canvas(root, cursor="cross", bg="gray")
canvas.pack(fill=tk.BOTH, expand=True)
root.withdraw()  # hide at start


def show_overlay():
    root.deiconify()


def hide_overlay():
    root.withdraw()
    canvas.delete("all")


def draw_rect():
    canvas.delete("all")
    if start_pos and current_pos:
        canvas.create_rectangle(
            start_pos[0], start_pos[1],
            current_pos[0], current_pos[1],
            outline="red", width=2
        )
    root.update()


# ----- Mouse -----
def on_mouse_move(x, y):
    global current_pos
    if selecting:
        current_pos = (x, y)
        draw_rect()


def on_mouse_click(x, y, button, pressed):
    global start_pos, current_pos
    if selecting and pressed:
        start_pos = (x, y)
        current_pos = (x, y)

    elif selecting and not pressed:  # mouse released
        if start_pos:
            hide_overlay()
            x1, y1 = start_pos
            x2, y2 = current_pos
            img = ImageGrab.grab(bbox=(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))
            text = extract_text(img)

            print("\nOUTPUT:")
            print(text if text else "No text found")
            if text:
                save_to_db(text)


# ----- Keyboard -----
def on_key_press(key):
    global selecting
    if key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
        selecting = True
        show_overlay()


def on_key_release(key):
    global selecting
    if key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
        selecting = False
        hide_overlay()


# ----- Start -----
def main():
    db()
    print("✅ Running OCR tool")
    print("👉 Hold CTRL and drag mouse to select area. Release mouse to capture.")
    print("👉 Rectangle will show during drag.")

    mouse_listener = mouse.Listener(on_move=on_mouse_move, on_click=on_mouse_click)
    keyboard_listener = keyboard.Listener(on_press=on_key_press, on_release=on_key_release)

    mouse_listener.start()
    keyboard_listener.start()

    root.mainloop()


if __name__ == "__main__":
    main()
