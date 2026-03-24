# Pet Portrait Harness

Local test harness for AI-generated line art via Gemini.

## Setup

```bash
cd pet-portrait-harness
pip install -r requirements.txt
export GEMINI_API_KEY="your-key-here"   # or set it in your shell profile
```

## Usage

### 1. CLI — single portrait
```bash
python generate.py path/to/photo.jpg "Buddy" --style classic
# → output/photo_classic_raw.png
# → output/photo_classic_buddy.png
```
Styles: `classic` · `minimal` · `naturalist`

### 2. Web UI
```bash
python app.py
# open http://localhost:5000
```
Drag-and-drop a photo, type the pet name, pick a style, click Generate.

### 3. Batch mode
```bash
# Drop photos into test_photos/, then:
python batch.py
# Generates all 3 styles for every photo → output/

# Override the name for all photos:
python batch.py --name "Buddy"
```
Filenames are used as pet names by default (`golden_max.jpg` → `Max`).

## Output files

| File | Description |
|------|-------------|
| `output/<stem>_<style>_raw.png` | Raw Gemini image, no compositing |
| `output/<stem>_<style>_<name>.png` | Final print-ready PNG with name |

## Model

`gemini-3.1-flash-image-preview` — change in `generate.py` if the name differs in your API tier.
