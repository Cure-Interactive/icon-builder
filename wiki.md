# PNG To ICO Wiki

PNG To ICO builds a Windows `.ico` file from PNG layers.

## Quick Start

```bash
python setup.py --venv
python png_to_ico.py
```

Manual install:

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python png_to_ico.py
```

On Linux or macOS, use `source .venv/bin/activate`.

## PNG Layer Discovery

The app detects PNG files whose names include a size token:

```text
icon_256x256.png
icon_128x128.png
icon_32x32.png
icon_16x16.png
```

Layers are ordered largest to smallest in the generated `.ico`.

## Project State

The app can save layer configuration into `config.json` inside the selected input directory. App-level recent-folder settings are stored in `config.json` beside `png_to_ico.py`. Runtime config is ignored by Git.

## Common Workflow

1. Start `png_to_ico.py`.
2. Select the input directory containing PNG layers.
3. Review discovered layers.
4. Add, remove, disable, or reorder layers if needed.
5. Build the `.ico` file.
