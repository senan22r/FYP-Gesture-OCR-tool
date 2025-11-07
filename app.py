from pynput import mouse, keyboard
from PIL import ImageGrab
import pytesseract
import numpy as np
import cv2
import sqlite3
import datetime
import time
import pyperclip

# ---- Tesseract PATH ----
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

selecting = False
start_pos = None
current_pos = None

# ---- DATABASE ----
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

# ---- OCR ----
def extract_text(image):
    gray = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2GRAY)
    text = pytesseract.image_to_string(gray)
    return text.strip()

# ---- MOUSE HANDLING ----
def on_mouse_move(x, y):
    global current_pos
    if selecting:
        current_pos = (x, y)

def on_mouse_click(x, y, button, pressed):
    global selecting, start_pos, current_pos

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
                pyperclip.copy(text)   # ✅ COPY TO CLIPBOARD
                print("✅ Copied to clipboard")

# ---- HOTKEY ----
def start_selection():
    global selecting
    selecting = True
    print("✅ CTRL+Q pressed — drag to select area…")

# ---- MAIN ----
def main():
    db()
    print("✅ OCR tool running.")
    print("👉 Hold CTRL + Q and drag with mouse.")
    print("👉 Release mouse to capture, OCR, save, and copy to clipboard.")

    mouse.Listener(on_move=on_mouse_move, on_click=on_mouse_click).start()

    hotkeys = keyboard.GlobalHotKeys({
        '<ctrl>+q': start_selection
    })
    hotkeys.start()

    while True:
        time.sleep(1)

if __name__ == "__main__":
    main()
