# PNG → ICO (Layered) — wiki.md

## What this tool does

Builds a Windows `.ico` file from **PNG layers** found in an **input directory**, where each layer’s filename includes a size token like `128x128` / `32x32`.

- **No default sizes.** Only what your filenames provide.
- Layers can be **auto-discovered** (scan the folder) and/or **manually configured** (add/replace/remove/enable/disable).
- Writes/updates a `config.json` **inside the input directory**.
- Always orders layers **largest → smallest** when building.

---

## Requirements

- **Python 3.10+** (3.11/3.12 recommended)
- Windows is the primary target (ICO), but it should run on macOS/Linux too.

Python packages:

- `Pillow`
- `icoutil`
- `customtkinter`

---

## Setup

### 1) Install Python

**Windows**

1. Install Python from python.org.
2. During install: check **“Add Python to PATH”**.

Verify:

```powershell
python --version
pip --version
````

---

### 2) Create a virtual environment (recommended)

From the folder where your script lives:

```powershell
python -m venv .venv
.\.venv\Scripts\activate
```

You should see `(.venv)` in your prompt.

---

### 3) Install dependencies

Create `requirements.txt` next to the script (or use the one provided), then:

```powershell
pip install -r requirements.txt
```

Optional sanity check:

```powershell
python -c "import PIL, icoutil, customtkinter; print('OK')"
```

---

## Folder layout expectations

### Minimal working folder

Your **input directory** must contain PNGs whose filenames include a `WxH` token:

Example:

```
MyIconLayers/
  layer_16x16.png
  layer_32x32.png
  layer_48x48.png
  layer_64x64.png
  layer_128x128.png
  config.json          (auto-created/updated)
```

Valid size token examples (the tool accepts these anywhere in the filename):

- `icon_128x128.png`
- `layer-32x32.png`
- `foo.16x16@2x.png` (still contains `16x16`)
- `anything_256x256_extra.png`

**Not valid (won’t be discovered):**

- `icon_128.png` (no `x`)
- `icon_128by128.png`
- `icon.png` (no token)

---

## How config.json works

`config.json` is stored **inside the input directory**.

Behavior:

- When you select an input directory:

  - If `config.json` exists → it loads it.
  - If not → it scans the directory, populates layers, and creates `config.json`.
- Any time you change:

  - output settings
  - layer enable/disable
  - add/replace/remove layers
  - rescan
    … the tool writes updates back to `config.json`.

### config.json format

```json
{
  "version": 1,
  "input_dir": ".",
  "output_dir": "C:/path/to/out",
  "output_name": "icon.ico",
  "layers": [
    { "size": "128x128", "path": "layer_128x128.png", "enabled": true },
    { "size": "64x64", "path": "layer_64x64.png", "enabled": true }
  ]
}
```

Notes:

- `path` is stored **relative** to the input directory when possible.
- If you force layers outside the input dir (currently blocked by UI), it would store absolute paths (not enabled in current build).

---

## Running the tool

### Run from terminal

Activate your venv first:

```powershell
.\.venv\Scripts\activate
python png_to_ico_gui.py
```

---

## Using the tool (simple → advanced)

## 1) Simple use (fastest path)

1. Put layer PNGs into a folder, named with size tokens:

   - `layer_16x16.png`
   - `layer_32x32.png`
   - `layer_48x48.png`
   - `layer_64x64.png`
   - `layer_128x128.png`
2. Launch the tool.
3. Click **Browse…** next to *Input Directory* and select your folder.
4. Click **Load/Refresh** (or it may load immediately if you used Browse).
5. Confirm layers appear in the list.
6. Set **Output Directory** and **ICO Name**.
7. Click **Build ICO**.

Result:

- `.ico` is written to output directory
- `config.json` created/updated in input folder

---

## 2) Auto-discovery workflow (normal)

Use this when you add/remove PNGs in the input folder.

1. Select input directory.
2. Click **Rescan Layers**.
3. The tool adds **new** discovered sizes to the list.
4. Click **Build ICO**.

What rescan does:

- Keeps your enabled/disabled choices from current list
- Adds new `WxH` files not already in your config
- Keeps missing entries (so you can fix them)

---

## 3) Manual configuration (custom layer set)

Use this when you want to:

- exclude certain sizes
- swap a file used for a size
- build a non-standard icon set (like only 128, 64, 32)

### Enable/disable

- Uncheck a layer to exclude it from the `.ico`.

### Replace a layer file

- Click **Replace…**
- Select another PNG in the input directory (must include `WxH` token in filename)
- That entry will adopt the new file and size token.

### Remove a layer

- Click **Remove**
- Entry disappears from the list and config.

### Add a layer

- Click **Add Layer…**
- Choose a PNG inside the input directory that has a `WxH` token.

---

## 4) Complex workflow: rebuilding layers across multiple projects

If you have multiple icon sets, each in its own folder:

```
Icons/
  AppA/
    layer_16x16.png ...
  AppB/
    layer_16x16.png ...
```

Workflow:

1. Open tool.
2. Browse input dir to `Icons/AppA`.
3. Build.
4. Browse input dir to `Icons/AppB`.
5. Build.

Each folder maintains its own `config.json`.

---

## Layer ordering rules (important)

When building:

- Layers are always sorted **largest → smallest** by:

  1. pixel area (W×H)
  2. width
  3. height

So even if the UI shows out of order temporarily, the build step forces correct order.

---

## Common mistakes / troubleshooting

### “No layers are enabled”

You unchecked everything. Enable at least one layer.

### “No enabled layer PNGs exist on disk”

At least one enabled layer:

- points to a missing file, or
- is not in the input directory anymore

Fix:

- Click **Rescan Layers**
- Or use **Replace…** on missing entries

### “Filename does not contain WxH token”

Your selected file doesn’t include something like `32x32`.

Fix:

- Rename the file to include `WxH`
- Example: `icon.png` → `layer_64x64.png`

### Duplicate sizes

If you have multiple files with the same `WxH` token:

- The build step currently **dedupes by size** and keeps the first (after largest→smallest sort).
- If you want strict behavior (error on duplicates, or pick newest, or pick config-first), update the logic.

---

## Best practices for high-quality icons

- Ensure each PNG is truly the correct size and crisp (don’t rely on upscaling).
- Common Windows sizes:

  - 16, 24, 32, 48, 64, 128, 256
- Prefer adding `256x256` for modern Windows support.

---

## Updating / uninstalling

### Update packages in venv

```powershell
pip install -U -r requirements.txt
```

### Remove venv

Delete the `.venv` folder.

---

## Advanced notes (implementation behavior)

- Discovery is **non-recursive**: only scans the top-level of the input directory.
- Manual add/replace currently requires files be **inside** the input directory (to keep config portable).
- Enabled layer PNGs are validated as PNGs before building.

---

## Suggested enhancements (if you want them next)

If you want any of these, say which ones and I’ll implement them:

1. **Recursive discovery** (search subfolders)
2. **Duplicate size policy**

   - error / pick config-first / pick newest file / pick largest filesize
3. **Allow layers outside input dir**
4. **Preview image thumbnails** per layer in UI
5. **Optional resizing mode**

   - If a size is missing, resize from best-fit larger layer
