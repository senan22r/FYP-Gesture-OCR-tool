import base64
import io
import json
import os
import urllib.request
import urllib.error

VISION_ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"
DEFAULT_KEY_FILE = "vision_key.txt"


def _load_api_key() -> str:
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
    with io.BytesIO() as output:
        image.save(output, format="PNG")
        return base64.b64encode(output.getvalue()).decode("utf-8")


def _call_vision_api(image) -> dict:
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
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
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
            if current and abs(center - current_center) > max(threshold, (current_height or 0) * 0.6):
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
                    current_center = (current_center * (len(current) - 1) + center) / len(current)
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
                    text = "".join(symbol.get("text", "") for symbol in word.get("symbols", []))
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
                    words_raw.append({
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
                    })
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
