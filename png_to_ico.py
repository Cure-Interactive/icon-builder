#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
png_to_ico_gui.py

Purpose
- Native GUI (CustomTkinter) for building a multi-layer Windows .ICO from PNG layers.
- Layers can come from:
  1) Automatic discovery in the selected input directory (regex WxH size token), and/or
  2) Manual user configuration in the UI:
     - choose any PNG file for a layer
     - set/override that layer's target size (W,H)

Layer rules
- Layers are always ordered largest → smallest (by target pixel area, then width/height).
- At build time, each enabled layer is resized to its configured target size (if needed).

Config behavior
- A `png_to_ico.json` is stored INSIDE the selected input directory.
- When an input directory is selected:
  - If `png_to_ico.json` exists, it is loaded and used to populate UI + selections.
  - If missing, the UI is populated from discovered layers and written to `png_to_ico.json`.
- The program keeps `png_to_ico.json` updated whenever:
  - layer list changes
  - enabled state changes
  - file path changes
  - target size changes
  - output settings change

Dependencies
- pip install pillow icoutil customtkinter

Notes
- Auto-discovery still uses filenames containing a "{W}x{H}" token, e.g., "icon_32x32.png".
- Manual per-layer file selection does NOT require a size token; target size is editable in UI.
- Paths in config are stored as relative to input directory when possible.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from PIL import Image
import icoutil

try:
  import customtkinter as ctk
except Exception as e:
  print("ERROR: customtkinter is required for this UI.\n  pip install customtkinter\n", file=sys.stderr)
  raise

import tkinter as tk
from tkinter import filedialog, messagebox


# =============================================================================
# Logging
# =============================================================================

from datetime import datetime

# Log files go to: <script_root>/_logs/png_to_ico_YYYY-MM-DD_HH-MM-SS.log
SCRIPT_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_ROOT_DIR, "_logs")
os.makedirs(LOG_DIR, exist_ok=True)

LOG_TS = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
LOG_FILENAME = f"png_to_ico_{LOG_TS}.log"
LOG_PATH = os.path.join(LOG_DIR, LOG_FILENAME)

logging.basicConfig(
  level=logging.INFO,
  format="%(asctime)s [%(levelname)s] %(message)s",
  handlers=[
    logging.FileHandler(LOG_PATH, encoding="utf-8"),
    logging.StreamHandler(sys.stdout),
  ],
)

logging.getLogger("PIL").setLevel(logging.WARNING)


# =============================================================================
# Constants / Regex
# =============================================================================

APP_TITLE = "PNG to ICO - Cure Interactive"
APP_USER_MODEL_ID = "CureInteractive.PNGToICO"
CONFIG_FILENAME = "png_to_ico.json"
APP_CONFIG_FILENAME = "config.json"
APP_CONFIG_PATH = os.path.join(SCRIPT_ROOT_DIR, APP_CONFIG_FILENAME)

# Match "000x000" style tokens anywhere in filename (1-4 digits each).
SIZE_TOKEN_RE = re.compile(r"(?P<w>\d{1,4})x(?P<h>\d{1,4})", re.IGNORECASE)

# =============================================================================
# Windows Taskbar Identity (AppUserModelID)
# =============================================================================

def set_windows_app_user_model_id(app_id: str) -> None:
  """
  Set an explicit Windows AppUserModelID for this process.

  Why this matters:
  - Windows uses AppUserModelID for taskbar grouping and (often) which icon is shown.
  - Without it, you may see the python.exe icon or inconsistent taskbar behavior.

  Notes:
  - No-op on non-Windows.
  - Best called BEFORE creating the Tk/CTk window (i.e., early in main()).
  """
  try:
    if os.name != "nt":
      return

    import ctypes  # stdlib

    # Windows API: https://learn.microsoft.com/windows/win32/shell/appids
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(str(app_id))
  except Exception:
    # Best-effort; app should still run
    return

# =============================================================================
# Window Icon (title bar / taskbar best-effort)
# =============================================================================

def set_window_icon(root, ico_path: str, png_path: str) -> None:
  """
  Set a title-bar icon with best-effort cross-platform behavior.

  Windows:
    - iconbitmap(.ico) works for title bar + taskbar in most cases.
  Linux/macOS:
    - iconphoto(.png) is the common path.

  Notes:
  - We try both; failures are ignored (best effort).
  - Paths should be absolute for reliability.
  """
  ico_abs = os.path.abspath(ico_path) if ico_path else ""
  png_abs = os.path.abspath(png_path) if png_path else ""

  try:
    if ico_abs and os.path.isfile(ico_abs):
      root.iconbitmap(ico_abs)
  except Exception:
    pass

  try:
    if png_abs and os.path.isfile(png_abs):
      img = tk.PhotoImage(file=png_abs)
      root.iconphoto(True, img)
      root._iconphoto_ref = img  # type: ignore[attr-defined]
  except Exception:
    pass


# =============================================================================
# Data models
# =============================================================================

@dataclass
class LayerItem:
  """
  Represents one layer entry in the UI/config.

  Attributes
  - target_size: (width, height) configured in UI/config for ICO layer.
  - source_rel_path: file path relative to input_dir when possible (else absolute).
  - enabled: whether to include this layer in ICO build.
  """
  target_size: Tuple[int, int]
  source_rel_path: str
  enabled: bool = True

  def size_key_desc(self) -> Tuple[int, int, int]:
    """
    Sort key for largest→smallest:
      - area desc
      - width desc
      - height desc
    """
    w, h = self.target_size
    return (w * h, w, h)


# =============================================================================
# Helpers: paths / config
# =============================================================================

def parse_size_token(name: str) -> Optional[Tuple[int, int]]:
  """
  Extract (W,H) from any string containing a 'WxH' size token.

  Args
  - name: filename or any string.

  Returns
  - (W,H) if found and valid; otherwise None.
  """
  m = SIZE_TOKEN_RE.search(name)
  if not m:
    return None
  try:
    w = int(m.group("w"))
    h = int(m.group("h"))
    if w <= 0 or h <= 0:
      return None
    return (w, h)
  except Exception:
    return None


def normalize_relpath(input_dir: str, path: str) -> str:
  """
  Convert an absolute path to a config-safe relative path (preferred).
  Falls back to absolute if relative cannot be computed.

  Args
  - input_dir: selected input directory (absolute).
  - path: file path (absolute or relative).

  Returns
  - relative path if inside input_dir; else absolute path.
  """
  input_dir_abs = os.path.abspath(input_dir)
  path_abs = os.path.abspath(os.path.join(input_dir_abs, path) if not os.path.isabs(path) else path)

  try:
    rel = os.path.relpath(path_abs, input_dir_abs)
    if rel.startswith(".."):
      return path_abs
    return rel
  except Exception:
    return path_abs


def resolve_path(input_dir: str, rel_or_abs: str) -> str:
  """
  Resolve a config-stored path to an absolute path.

  Args
  - input_dir: selected input directory (absolute).
  - rel_or_abs: relative-to-input path or absolute.

  Returns
  - absolute path.
  """
  if os.path.isabs(rel_or_abs):
    return rel_or_abs
  return os.path.abspath(os.path.join(os.path.abspath(input_dir), rel_or_abs))


def config_path_for_input_dir(input_dir: str) -> str:
  """
  Get png_to_ico.json full path inside input directory.
  """
  return os.path.join(os.path.abspath(input_dir), CONFIG_FILENAME)


def load_config(input_dir: str) -> Optional[dict]:
  """
  Load png_to_ico.json from input directory, if it exists.

  Returns
  - dict if loaded; otherwise None.
  """
  p = config_path_for_input_dir(input_dir)
  if not os.path.isfile(p):
    return None
  try:
    with open(p, "r", encoding="utf-8") as f:
      return json.load(f)
  except Exception as e:
    logging.warning("Failed to load config: %s (%s)", p, e)
    return None


def save_config(input_dir: str, data: dict) -> None:
  """
  Save png_to_ico.json in the input directory.
  Intended to be called often to "keep config updated".
  """
  p = config_path_for_input_dir(input_dir)
  try:
    with open(p, "w", encoding="utf-8") as f:
      json.dump(data, f, indent=2)
  except Exception as e:
    logging.warning("Failed to save config: %s (%s)", p, e)


def _read_json(path: str) -> dict:
  try:
    if not os.path.isfile(path):
      return {}
    with open(path, "r", encoding="utf-8") as f:
      data = json.load(f)
    return data if isinstance(data, dict) else {}
  except Exception:
    return {}


def _write_json_atomic(path: str, data: dict) -> None:
  try:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
      json.dump(data, f, indent=2)
    os.replace(tmp, path)
  except Exception:
    # Best-effort; app should still run
    return


def _norm_dir(p: str) -> str:
  return os.path.normpath(os.path.abspath(p))


def _dedupe_keep_order(items: List[str]) -> List[str]:
  seen = set()
  out: List[str] = []
  for x in items:
    if x in seen:
      continue
    seen.add(x)
    out.append(x)
  return out


def _filter_existing_dirs(items: List[str]) -> List[str]:
  out: List[str] = []
  for p in items:
    try:
      if os.path.isdir(p):
        out.append(p)
    except Exception:
      pass
  return out


def layers_to_config(layers: List[LayerItem]) -> List[dict]:
  """
  Serialize layer items for png_to_ico.json.
  """
  out: List[dict] = []
  for it in layers:
    w, h = it.target_size
    out.append({
      # New (explicit)
      "target_size": f"{w}x{h}",
      # Back-compat for older config readers
      "size": f"{w}x{h}",
      "path": it.source_rel_path,
      "enabled": bool(it.enabled),
    })
  return out


def layers_from_config(data: dict) -> List[LayerItem]:
  """
  Deserialize layer items from png_to_ico.json structure.

  Accepts:
  - "target_size" (preferred) or "size" (legacy)
  - "path" for source image
  """
  raw = data.get("layers", [])
  out: List[LayerItem] = []

  if not isinstance(raw, list):
    return out

  for entry in raw:
    if not isinstance(entry, dict):
      continue

    size_s = entry.get("target_size", entry.get("size", ""))
    p = entry.get("path", "")
    en = bool(entry.get("enabled", True))

    if not isinstance(size_s, str) or not isinstance(p, str):
      continue

    size = parse_size_token(size_s)
    if not size:
      continue

    out.append(LayerItem(target_size=size, source_rel_path=p, enabled=en))

  return out


# =============================================================================
# Default target sizes (largest → smallest)
# =============================================================================

# Windows ICO standard max size is 256x256 for widest compatibility.
ICO_MAX_PNG_SIZE = 256

STANDARD_TARGET_SIZES = [256, 128, 64, 48, 32, 16]


def gather_png_sources_with_dims(input_dir: str) -> List[Tuple[int, int, str]]:
  """
  Collect candidate PNG sources in the input dir with actual dimensions.

  Returns
  - list of (w, h, rel_path)
  """
  out: List[Tuple[int, int, str]] = []
  if not input_dir or not os.path.isdir(input_dir):
    return out

  for name in os.listdir(input_dir):
    if not name.lower().endswith(".png"):
      continue

    full = os.path.join(input_dir, name)
    if not os.path.isfile(full):
      continue

    # Prefer filename token, else read actual size
    size = parse_size_token(name)
    if not size:
      size = infer_png_size(full)
    if not size:
      continue

    w, h = size
    if w <= 0 or h <= 0:
      continue

    out.append((w, h, normalize_relpath(input_dir, full)))

  return out


def pick_largest_qualifying_source(
  target: Tuple[int, int],
  sources: List[Tuple[int, int, str]],
) -> Optional[str]:
  """
  For a given target (tw, th), pick:
  - the LARGEST source that qualifies (w>=tw and h>=th)
  - else the LARGEST source overall (fallback)
  """
  if not sources:
    return None

  tw, th = target

  qualifying = [(w, h, p) for (w, h, p) in sources if w >= tw and h >= th]
  pool = qualifying if qualifying else sources

  # Largest by area, then by width/height
  w, h, p = max(pool, key=lambda x: (x[0] * x[1], x[0], x[1]))
  return p


def build_default_layers_from_sources(input_dir: str) -> List[LayerItem]:
  """
  Create default layer rows (targets) based on what sources exist.
  - Includes standard targets up to the largest available dimension.
  - Always returns at least [16,32,48,64,128] if possible, and 256 if available.
  - Each row gets auto-assigned a best source file.
  """
  sources = gather_png_sources_with_dims(input_dir)
  if not sources:
    return []

  # Use the largest "square-safe" dimension for deciding which targets to include
  max_square_dim = max(min(w, h) for (w, h, _p) in sources)

  targets = [s for s in STANDARD_TARGET_SIZES if s <= max_square_dim]
  if not targets:
    # If nothing reaches 16x16, still create 16 as a target and fallback to largest source
    targets = [16]

  layers: List[LayerItem] = []
  for s in targets:
    target = (s, s)
    best = pick_largest_qualifying_source(target, sources)
    if not best:
      continue
    layers.append(LayerItem(target_size=target, source_rel_path=best, enabled=True))

  return sort_layers_desc(layers)


# =============================================================================
# Discovery / merge rules
# =============================================================================

def discover_layers_in_dir(input_dir: str) -> Dict[Tuple[int, int], str]:
  """
  Discover PNG layers in input directory by parsing any 'WxH' size token.

  Returns
  - dict[(W,H)] = relative path (preferred) or absolute if needed.

  Tie-breaker
  - If multiple files produce same size: keep the first encountered (stable enough).
    Users can override by editing file/target size in UI.
  """
  found: Dict[Tuple[int, int], str] = {}

  if not input_dir or not os.path.isdir(input_dir):
    return found

  for name in os.listdir(input_dir):
    if not name.lower().endswith(".png"):
      continue
    size = parse_size_token(name)
    if not size:
      continue
    full = os.path.join(input_dir, name)
    if not os.path.isfile(full):
      continue
    if size not in found:
      found[size] = normalize_relpath(input_dir, full)

  return found


def merge_config_layers_with_discovery(
  input_dir: str,
  cfg_layers: List[LayerItem],
  discovered: Dict[Tuple[int, int], str],
) -> List[LayerItem]:
  """
  Merge layers loaded from config with newly discovered layers.

  Behavior
  - Preserve config ordering/enablement for sizes it already knows.
  - For newly discovered sizes not in config, add them enabled=True, target_size=size.
  - For config entries whose files are missing, keep them (so user can fix).

  Returns
  - merged list (not yet sorted).
  """
  out: List[LayerItem] = []
  seen_sizes: set = set()

  for it in cfg_layers:
    out.append(it)
    seen_sizes.add(it.target_size)

  for size, relp in discovered.items():
    if size in seen_sizes:
      continue
    out.append(LayerItem(target_size=size, source_rel_path=relp, enabled=True))
    seen_sizes.add(size)

  for it in out:
    it.source_rel_path = normalize_relpath(input_dir, resolve_path(input_dir, it.source_rel_path))

  return out


def sort_layers_desc(layers: List[LayerItem]) -> List[LayerItem]:
  """
  Always order largest → smallest (by target size).
  """
  return sorted(layers, key=lambda it: it.size_key_desc(), reverse=True)


def infer_png_size(path: str) -> Optional[Tuple[int, int]]:
  """
  Read PNG dimensions from disk.

  Returns
  - (W,H) or None if unreadable.
  """
  try:
    with Image.open(path) as img:
      w, h = img.size
      if w > 0 and h > 0:
        return (int(w), int(h))
  except Exception:
    return None
  return None


# =============================================================================
# ICO build (with per-layer resizing)
# =============================================================================

def build_ico_from_layers(
  input_dir: str,
  layers: List[LayerItem],
  output_dir: str,
  output_name: str,
) -> str:
  """
  Build an .ICO from enabled layers in the list.

  Behavior
  - Layers are added in largest → smallest order (by target size).
  - Only enabled layers with existing source files are included.
  - Each layer image is resized to its target size (if needed) before adding to the ICO.

  Returns
  - absolute path to the created ICO
  """
  if not output_name.lower().endswith(".ico"):
    output_name += ".ico"

  out_dir_abs = os.path.abspath(output_dir)
  os.makedirs(out_dir_abs, exist_ok=True)
  ico_path = os.path.abspath(os.path.join(out_dir_abs, output_name))

  enabled = [it for it in sort_layers_desc(layers) if it.enabled]

  usable: List[Tuple[LayerItem, str]] = []
  for it in enabled:
    full = resolve_path(input_dir, it.source_rel_path)
    if os.path.isfile(full):
      usable.append((it, full))

  if not usable:
    raise ValueError("No enabled layer PNGs exist on disk. Fix paths or add layers.")

  # If duplicate target sizes exist enabled, keep first (largest-first order still).
  seen_sizes: set = set()
  unique: List[Tuple[LayerItem, str]] = []
  for it, full in usable:
    if it.target_size in seen_sizes:
      continue
    unique.append((it, full))
    seen_sizes.add(it.target_size)

  ico = icoutil.IcoFile()

  # Temp files for resized PNGs, so icoutil can ingest them by path.
  temp_paths: List[str] = []
  try:
    for it, full in unique:
      tw, th = it.target_size
      if tw <= 0 or th <= 0:
        raise ValueError(f"Invalid target size for layer: {tw}x{th}")

      if tw > ICO_MAX_PNG_SIZE or th > ICO_MAX_PNG_SIZE:
        raise ValueError(
          f"Invalid ICO layer size {tw}x{th}. "
          f"Max supported size is {ICO_MAX_PNG_SIZE}x{ICO_MAX_PNG_SIZE}. "
          "Set the Target size to 256 or smaller."
        )

      with Image.open(full) as img:
        img = img.convert("RGBA")
        if img.size != (tw, th):
          img = img.resize((tw, th), resample=Image.LANCZOS)

        fd, tmp_path = tempfile.mkstemp(prefix="png_to_ico_layer_", suffix=".png")
        os.close(fd)

        img.save(tmp_path, format="PNG", optimize=True)
        temp_paths.append(tmp_path)

      ico.add_png(tmp_path)
      logging.info("Add layer %dx%d from %s", tw, th, full)

    ico.write(ico_path)
    logging.info("ICO written: %s", ico_path)
  finally:
    for p in temp_paths:
      try:
        os.remove(p)
      except Exception:
        pass

  return ico_path


# =============================================================================
# UI
# =============================================================================

class App(ctk.CTk):
  """
  CustomTkinter application for managing layers and building ICO files.

  UI features
  - Select input directory (contains png_to_ico.json, may contain discovered layers).
  - Rescan layers (auto-discovery from filenames containing WxH).
  - Per-layer controls:
    - Enable/disable
    - Choose source PNG file (any)
    - Set target size (W,H)
    - Remove layer
  - Output settings:
    - output directory
    - output file name
  - Config auto-load/save in input directory.
  """

  def __init__(self) -> None:
    super().__init__()

    set_window_icon(
      self,
      os.path.join(SCRIPT_ROOT_DIR, "icon.ico"),
      os.path.join(SCRIPT_ROOT_DIR, "icon.png"),
    )

    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")

    self.title(APP_TITLE)
    self.geometry("1040x680")

    self.input_dir: str = ""
    self.layers: List[LayerItem] = []

    # Recent input directories (stored beside this script, not in the input dir)
    app_cfg = _read_json(APP_CONFIG_PATH)

    # Support 16 previous dirs by default.
    self.recent_input_dirs_max = int(app_cfg.get("recent_input_dirs_max", 16) or 16)
    if self.recent_input_dirs_max <= 0:
      self.recent_input_dirs_max = 16

    raw = app_cfg.get("recent_input_dirs", [])
    if not isinstance(raw, list):
      raw = []

    self.recent_input_dirs: List[str] = []
    for p in raw:
      if isinstance(p, str) and p.strip():
        self.recent_input_dirs.append(_norm_dir(p.strip()))

    self.recent_input_dirs = _dedupe_keep_order(self.recent_input_dirs)
    self.recent_input_dirs = _filter_existing_dirs(self.recent_input_dirs)
    self.recent_input_dirs = self.recent_input_dirs[: self.recent_input_dirs_max]

    self.recent_input_dir_var = tk.StringVar(
      value=(self.recent_input_dirs[0] if self.recent_input_dirs else "(none)")
    )

    # Recent list UX state
    self.recent_max_var = tk.StringVar(value=str(self.recent_input_dirs_max))
    self._loading_recent_select = False

    # Persist cleaned startup state
    self._persist_app_config()

    self.output_dir_var = tk.StringVar(value=os.path.abspath(os.getcwd()))
    self.output_name_var = tk.StringVar(value="icon.ico")

    self._build_top_controls()
    self._build_layers_editor()
    self._build_bottom_controls()

    # Auto-load the most recent project (if any) on startup.
    if self.recent_input_dirs:
      try:
        self.load_or_init_from_input_dir(self.recent_input_dirs[0])
      except Exception as e:
        logging.warning("Auto-load recent project failed: %s", e)

    # Auto-load most recent project (if any) on startup.
    try:
      if self.recent_input_dirs and os.path.isdir(self.recent_input_dirs[0]):
        self.load_or_init_from_input_dir(self.recent_input_dirs[0])
    except Exception:
      # Best-effort startup behavior; do not prevent app launch.
      pass

  # ---------------------------------------------------------------------------
  # UI construction
  # ---------------------------------------------------------------------------

  def _build_top_controls(self) -> None:
    self.top = ctk.CTkFrame(self)
    self.top.pack(fill="x", padx=12, pady=(12, 6))

    # Editable dropdown: user can type a path, or pick from recent history.
    self.input_dir_var = tk.StringVar(
      value=(self.recent_input_dirs[0] if getattr(self, "recent_input_dirs", None) else "")
    )

    ctk.CTkLabel(self.top, text="Input Directory:").grid(
      row=0,
      column=0,
      sticky="w",
      padx=8,
      pady=8,
    )

    # ttk.Combobox is the most reliable "string field dropdown" across CTk versions.
    self.input_entry = ctk.CTkComboBox(
      self.top,
      variable=self.input_dir_var,
      values=list(self.recent_input_dirs) if self.recent_input_dirs else [],
      command=self._on_input_dir_combo_selected,
      state="normal",
    )
    self.input_entry.grid(row=0, column=1, sticky="ew", padx=8, pady=8)

    self.btn_browse_input = ctk.CTkButton(self.top, text="Browse…", command=self.on_browse_input_dir, width=110)
    self.btn_browse_input.grid(row=0, column=2, padx=8, pady=8)

    self.btn_load = ctk.CTkButton(self.top, text="Load/Refresh", command=self.on_load_input_dir, width=120)
    self.btn_load.grid(row=0, column=3, padx=8, pady=8)

    self.btn_rescan = ctk.CTkButton(self.top, text="Rescan Layers", command=self.on_rescan_layers, width=130)
    self.btn_rescan.grid(row=0, column=4, padx=8, pady=8)

    # Keep the "clear history" functionality, just move it onto the top row.
    self.btn_clear_recent = ctk.CTkButton(self.top, text="Clear History", command=self.on_clear_recent, width=120)
    self.btn_clear_recent.grid(row=0, column=5, padx=8, pady=8)

    self.top.grid_columnconfigure(1, weight=1)

  def _build_layers_editor(self) -> None:
    self.mid = ctk.CTkFrame(self)
    self.mid.pack(fill="both", expand=True, padx=12, pady=6)

    header = ctk.CTkFrame(self.mid)
    header.pack(fill="x", padx=8, pady=(8, 6))

    ctk.CTkLabel(
      header,
      text="Layers (largest → smallest) — each layer has a source PNG + a target size (W,H)",
    ).pack(side="left", padx=8)

    self.btn_add_layer = ctk.CTkButton(header, text="Add Layer…", command=self.on_add_layer, width=120)
    self.btn_add_layer.pack(side="right", padx=8)

    self.layer_scroll = ctk.CTkScrollableFrame(self.mid)
    self.layer_scroll.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    self._layer_rows: List[dict] = []

  def _build_bottom_controls(self) -> None:
    self.bottom = ctk.CTkFrame(self)
    self.bottom.pack(fill="x", padx=12, pady=(6, 12))

    ctk.CTkLabel(self.bottom, text="Output Directory:").grid(row=0, column=0, sticky="w", padx=8, pady=8)
    self.out_dir_entry = ctk.CTkEntry(self.bottom, textvariable=self.output_dir_var)
    self.out_dir_entry.grid(row=0, column=1, sticky="ew", padx=8, pady=8)
    ctk.CTkButton(self.bottom, text="Browse…", command=self.on_browse_output_dir, width=110).grid(row=0, column=2, padx=8, pady=8)

    ctk.CTkLabel(self.bottom, text="ICO Name:").grid(row=1, column=0, sticky="w", padx=8, pady=8)
    self.out_name_entry = ctk.CTkEntry(self.bottom, textvariable=self.output_name_var)
    self.out_name_entry.grid(row=1, column=1, sticky="ew", padx=8, pady=8)

    self.btn_build = ctk.CTkButton(self.bottom, text="Build ICO", command=self.on_build_ico, width=140)
    self.btn_build.grid(row=1, column=2, padx=8, pady=8)

    self.bottom.grid_columnconfigure(1, weight=1)

    self.output_dir_var.trace_add("write", lambda *_: self.persist_config_if_possible())
    self.output_name_var.trace_add("write", lambda *_: self.persist_config_if_possible())

  # ---------------------------------------------------------------------------
  # Config IO
  # ---------------------------------------------------------------------------

  def make_config_dict(self) -> dict:
    return {
      "version": 2,
      "input_dir": ".",  # implicit; config lives inside input_dir
      "output_dir": self.output_dir_var.get(),
      "output_name": self.output_name_var.get(),
      "layers": layers_to_config(sort_layers_desc(self.layers)),
    }

  def persist_config_if_possible(self) -> None:
    if not self.input_dir or not os.path.isdir(self.input_dir):
      return
    save_config(self.input_dir, self.make_config_dict())

  def load_or_init_from_input_dir(self, input_dir: str) -> None:
    self.input_dir = os.path.abspath(input_dir)
    self.input_dir_var.set(self.input_dir)

    cfg = load_config(self.input_dir)
    discovered = discover_layers_in_dir(self.input_dir)

    default_out_dir = self.input_dir

    if cfg:
      cfg_layers = layers_from_config(cfg)
      merged = merge_config_layers_with_discovery(self.input_dir, cfg_layers, discovered)

      out_dir = cfg.get("output_dir")
      out_name = cfg.get("output_name")

      # If project config contains an invalid output directory:
      # - alert the user
      # - after OK, reset output to the selected project directory
      if isinstance(out_dir, str) and out_dir.strip():
        out_dir_abs = os.path.abspath(out_dir.strip())
        if os.path.isdir(out_dir_abs):
          self.output_dir_var.set(out_dir_abs)
        else:
          messagebox.showwarning(
            APP_TITLE,
            "Output directory in this project's config is invalid:\n\n"
            f"{out_dir_abs}\n\n"
            "It will be reset to the project directory."
          )
          self.output_dir_var.set(default_out_dir)
      else:
        self.output_dir_var.set(default_out_dir)

      if isinstance(out_name, str) and out_name.strip():
        self.output_name_var.set(out_name)

      self.layers = sort_layers_desc(merged)
    else:
      # NEW: Default layer rows + auto-pick best source for each target.
      self.layers = build_default_layers_from_sources(self.input_dir)

      # Fallback: if no readable PNGs, keep old discovery behavior
      if not self.layers:
        init_layers: List[LayerItem] = []
        for size, relp in discovered.items():
          init_layers.append(LayerItem(target_size=size, source_rel_path=relp, enabled=True))
        self.layers = sort_layers_desc(init_layers)

      self.output_dir_var.set(default_out_dir)

    self.persist_config_if_possible()
    self.refresh_layer_rows()

  # ---------------------------------------------------------------------------
  # Layers UI render
  # ---------------------------------------------------------------------------

  def clear_layer_rows(self) -> None:
    for row in self._layer_rows:
      try:
        row["frame"].destroy()
      except Exception:
        pass
    self._layer_rows = []

  def _try_set_layer_size(self, idx: int, w_s: str, h_s: str) -> None:
    """
    Parse/validate width/height strings and update the layer target size if valid.
    """
    try:
      w = int(str(w_s).strip())
      h = int(str(h_s).strip())
      if w <= 0 or h <= 0:
        return

      if w > ICO_MAX_PNG_SIZE or h > ICO_MAX_PNG_SIZE:
        messagebox.showerror(
          APP_TITLE,
          f"Invalid ICO target size {w}x{h}.\n\n"
          f"Max supported size is {ICO_MAX_PNG_SIZE}x{ICO_MAX_PNG_SIZE}."
        )
        return

      self.layers[idx].target_size = (w, h)
      self.layers = sort_layers_desc(self.layers)
      self.persist_config_if_possible()
      self.refresh_layer_rows()
    except Exception:
      return

  def refresh_layer_rows(self) -> None:
    """
    Rebuild the scrollable layer list rows from self.layers.
    Always sorts largest → smallest.
    """
    self.layers = sort_layers_desc(self.layers)
    self.clear_layer_rows()

    for idx, it in enumerate(self.layers):
      frame = ctk.CTkFrame(self.layer_scroll)
      frame.pack(fill="x", padx=6, pady=4)

      enabled_var = tk.BooleanVar(value=bool(it.enabled))

      def _make_on_toggle(i: int, var: tk.BooleanVar):
        def _on_toggle():
          self.layers[i].enabled = bool(var.get())
          self.persist_config_if_possible()
        return _on_toggle

      chk = ctk.CTkCheckBox(frame, text="", variable=enabled_var, command=_make_on_toggle(idx, enabled_var), width=24)
      chk.grid(row=0, column=0, padx=(8, 4), pady=8)

      # Target size editors
      tw, th = it.target_size
      w_var = tk.StringVar(value=str(tw))
      h_var = tk.StringVar(value=str(th))

      ctk.CTkLabel(frame, text="Target:", width=60).grid(row=0, column=1, padx=(6, 2), pady=8, sticky="w")
      ent_w = ctk.CTkEntry(frame, width=70, textvariable=w_var)
      ent_w.grid(row=0, column=2, padx=(2, 2), pady=8, sticky="w")
      ctk.CTkLabel(frame, text="x", width=14).grid(row=0, column=3, padx=(2, 2), pady=8, sticky="w")
      ent_h = ctk.CTkEntry(frame, width=70, textvariable=h_var)
      ent_h.grid(row=0, column=4, padx=(2, 10), pady=8, sticky="w")

      def _make_on_size_commit(i: int, wv: tk.StringVar, hv: tk.StringVar):
        def _commit(_evt=None):
          self._try_set_layer_size(i, wv.get(), hv.get())
        return _commit

      ent_w.bind("<FocusOut>", _make_on_size_commit(idx, w_var, h_var))
      ent_h.bind("<FocusOut>", _make_on_size_commit(idx, w_var, h_var))
      ent_w.bind("<Return>", _make_on_size_commit(idx, w_var, h_var))
      ent_h.bind("<Return>", _make_on_size_commit(idx, w_var, h_var))

      # Path + missing indicator (+ actual PNG dimensions after the name)
      full = resolve_path(self.input_dir, it.source_rel_path) if self.input_dir else it.source_rel_path
      missing = (self.input_dir and not os.path.isfile(full))

      path_text = it.source_rel_path

      # If the file exists, append its actual pixel dimensions after the filename.
      if not missing and full and os.path.isfile(full):
        size = infer_png_size(full)
        if size:
          sw, sh = size
          path_text += f"  ({sw}x{sh})"

      if missing:
        path_text += "  (MISSING)"

      lbl = ctk.CTkLabel(frame, text=path_text, anchor="w")
      lbl.grid(row=0, column=5, padx=6, pady=8, sticky="ew")

      def _make_on_pick_file(i: int):
        def _on_pick():
          self.on_replace_layer_file(i)
        return _on_pick

      def _make_on_remove(i: int):
        def _on_remove():
          self.on_remove_layer(i)
        return _on_remove

      btn_pick = ctk.CTkButton(frame, text="File…", command=_make_on_pick_file(idx), width=90)
      btn_pick.grid(row=0, column=6, padx=6, pady=8)

      btn_remove = ctk.CTkButton(frame, text="Remove", command=_make_on_remove(idx), width=90)
      btn_remove.grid(row=0, column=7, padx=(6, 8), pady=8)

      frame.grid_columnconfigure(5, weight=1)

      self._layer_rows.append({
        "frame": frame,
        "enabled_var": enabled_var,
        "w_var": w_var,
        "h_var": h_var,
      })

  # ---------------------------------------------------------------------------
  # UI callbacks
  # ---------------------------------------------------------------------------

  def _persist_app_config(self) -> None:
    """
    Persist app-level config (recent dirs + max) beside this script.
    """
    _write_json_atomic(APP_CONFIG_PATH, {
      "recent_input_dirs_max": int(self.recent_input_dirs_max),
      "recent_input_dirs": list(self.recent_input_dirs),
    })

  def _update_recent_menu(self) -> None:
    """
    Refresh the option menu values to match self.recent_input_dirs.
    """
    values = self.recent_input_dirs if self.recent_input_dirs else ["(none)"]
    try:
      self.recent_menu.configure(values=values)
    except Exception:
      pass

    # If current selection is invalid, force a safe value
    cur = str(self.recent_input_dir_var.get() or "").strip()
    if not cur or cur not in values:
      self.recent_input_dir_var.set(values[0])

  def _remember_input_dir(self, p: str) -> None:
    """
    Add directory to recent list (front), dedupe, trim to max, persist + refresh UI.
    """
    if not p:
      return

    p_norm = _norm_dir(p)
    if not os.path.isdir(p_norm):
      return

    # Move to front
    items = [p_norm] + [x for x in self.recent_input_dirs if x != p_norm]
    items = _dedupe_keep_order(items)
    items = _filter_existing_dirs(items)
    items = items[: self.recent_input_dirs_max]

    self.recent_input_dirs = items

    # Avoid triggering on_recent_selected while we programmatically set it
    self._loading_recent_select = True
    try:
      self.recent_input_dir_var.set(p_norm)
      self._update_recent_menu()
    finally:
      self._loading_recent_select = False

    self._persist_app_config()

  def _apply_recent_max_from_entry(self) -> None:
    """
    Parse/validate 'Keep last' entry, update max + trim list + persist + refresh menu.
    """
    raw = str(self.recent_max_var.get() or "").strip()
    try:
      n = int(raw)
    except Exception:
      n = self.recent_input_dirs_max

    # Clamp to sane bounds
    if n <= 0:
      n = 1
    if n > 200:
      n = 200

    self.recent_input_dirs_max = n
    self.recent_max_var.set(str(n))

    # Trim list to new max
    self.recent_input_dirs = self.recent_input_dirs[: self.recent_input_dirs_max]
    self._update_recent_menu()
    self._persist_app_config()

  def on_recent_selected(self, choice: str) -> None:
    """
    OptionMenu callback: selecting a recent dir loads it.
    """
    if self._loading_recent_select:
      return

    p = str(choice or "").strip()
    if not p or p == "(none)":
      return

    if not os.path.isdir(p):
      # Remove dead entry and refresh
      self.recent_input_dirs = [x for x in self.recent_input_dirs if x != p]
      self._update_recent_menu()
      self._persist_app_config()
      messagebox.showerror(APP_TITLE, f"Recent directory no longer exists:\n\n{p}")
      return

    self.input_dir_var.set(p)
    self._remember_input_dir(p)
    self.load_or_init_from_input_dir(p)

  def on_clear_recent(self) -> None:
    """
    Clear recent dir history.
    """
    self.recent_input_dirs = []
    self._loading_recent_select = True
    try:
      self.recent_input_dir_var.set("(none)")
      self._update_recent_menu()
    finally:
      self._loading_recent_select = False

    self._persist_app_config()

  def _persist_recent_input_dirs(self) -> None:
    _write_json_atomic(APP_CONFIG_PATH, {
      "recent_input_dirs_max": int(self.recent_input_dirs_max),
      "recent_input_dirs": list(self.recent_input_dirs),
    })

  def _refresh_input_dir_dropdown(self) -> None:
    self.input_entry.configure(values=list(self.recent_input_dirs))

  def _remember_input_dir(self, p: str) -> None:
    if not p:
      return

    p_norm = _norm_dir(p)
    if not os.path.isdir(p_norm):
      return

    # Move to front + dedupe + trim
    items = [p_norm] + [x for x in self.recent_input_dirs if x != p_norm]
    items = _dedupe_keep_order(items)
    items = _filter_existing_dirs(items)
    items = items[: self.recent_input_dirs_max]

    self.recent_input_dirs = items
    self._persist_recent_input_dirs()
    self._refresh_input_dir_dropdown()

  def _on_input_dir_combo_selected(self, choice: str) -> None:
    # When user picks a recent dir from dropdown, load it immediately.
    self.on_load_input_dir()

  def on_clear_recent(self) -> None:
    self.recent_input_dirs = []
    self._persist_recent_input_dirs()
    self._refresh_input_dir_dropdown()

  def on_browse_input_dir(self) -> None:
    cur = str(self.input_dir_var.get() or "").strip()
    initialdir = cur if (cur and os.path.isdir(cur)) else (self.input_dir if (self.input_dir and os.path.isdir(self.input_dir)) else os.path.abspath(os.getcwd()))
    p = filedialog.askdirectory(title="Select input directory (contains png_to_ico.json)", initialdir=initialdir)
    if not p:
      return
    self.input_dir_var.set(os.path.abspath(p))
    self.on_load_input_dir()

  def on_load_input_dir(self) -> None:
    p = self.input_dir_var.get().strip()
    if not p or not os.path.isdir(p):
      messagebox.showerror(APP_TITLE, "Input directory is missing or invalid.")
      return

    self._remember_input_dir(p)
    self.load_or_init_from_input_dir(p)

  def on_rescan_layers(self) -> None:
    if not self.input_dir or not os.path.isdir(self.input_dir):
      messagebox.showerror(APP_TITLE, "Select a valid input directory first.")
      return

    discovered = discover_layers_in_dir(self.input_dir)
    cfg_layers = self.layers[:]  # current state is the "config" state
    merged = merge_config_layers_with_discovery(self.input_dir, cfg_layers, discovered)
    self.layers = sort_layers_desc(merged)

    self.persist_config_if_possible()
    self.refresh_layer_rows()

  def on_browse_output_dir(self) -> None:
    cur = str(self.output_dir_var.get() or "").strip()
    initialdir = cur if (cur and os.path.isdir(cur)) else (self.input_dir if (self.input_dir and os.path.isdir(self.input_dir)) else os.path.abspath(os.getcwd()))
    p = filedialog.askdirectory(title="Select output directory", initialdir=initialdir)
    if not p:
      return
    self.output_dir_var.set(os.path.abspath(p))

  def on_add_layer(self) -> None:
    """
    Add a new layer by selecting a PNG file.

    New behavior:
    - Any PNG is allowed (no filename token required).
    - Initial target size is chosen by:
      1) WxH token in filename, else
      2) actual PNG dimensions.
    """
    if not self.input_dir or not os.path.isdir(self.input_dir):
      messagebox.showerror(APP_TITLE, "Select a valid input directory first.")
      return

    p = filedialog.askopenfilename(
      title="Select layer PNG (any PNG; target size is editable)",
      initialdir=self.input_dir,
      filetypes=[("PNG images", "*.png"), ("All files", "*.*")],
    )
    if not p:
      return

    p_abs = os.path.abspath(p)
    rel_path = normalize_relpath(self.input_dir, p_abs)

    size = parse_size_token(os.path.basename(p_abs))
    if not size:
      size = infer_png_size(p_abs)

    if not size:
      messagebox.showerror(APP_TITLE, "Could not read PNG dimensions. The file may be invalid.")
      return

    w0, h0 = size
    if w0 > ICO_MAX_PNG_SIZE or h0 > ICO_MAX_PNG_SIZE:
      # Default to a sane ICO target instead of inheriting a too-large source dimension.
      size = (ICO_MAX_PNG_SIZE, ICO_MAX_PNG_SIZE)

    self.layers.append(LayerItem(target_size=size, source_rel_path=rel_path, enabled=True))

    self.layers = sort_layers_desc(self.layers)

    self.persist_config_if_possible()
    self.refresh_layer_rows()

  def on_replace_layer_file(self, idx: int) -> None:
    """
    Pick/replace the source PNG for a layer.

    Notes:
    - Any PNG is allowed.
    - Target size is NOT changed automatically (you control it via the Target W/H fields).
    """
    if not self.input_dir or not os.path.isdir(self.input_dir):
      messagebox.showerror(APP_TITLE, "Select a valid input directory first.")
      return

    current = self.layers[idx]
    p = filedialog.askopenfilename(
      title="Select source PNG for this layer",
      initialdir=self.input_dir,
      filetypes=[("PNG images", "*.png"), ("All files", "*.*")],
    )
    if not p:
      return

    p_abs = os.path.abspath(p)
    current.source_rel_path = normalize_relpath(self.input_dir, p_abs)

    self.layers = sort_layers_desc(self.layers)
    self.persist_config_if_possible()
    self.refresh_layer_rows()

  def on_remove_layer(self, idx: int) -> None:
    try:
      self.layers.pop(idx)
    except Exception:
      return
    self.layers = sort_layers_desc(self.layers)
    self.persist_config_if_possible()
    self.refresh_layer_rows()

  def on_build_ico(self) -> None:
    if not self.input_dir or not os.path.isdir(self.input_dir):
      messagebox.showerror(APP_TITLE, "Select a valid input directory first.")
      return

    out_dir = self.output_dir_var.get().strip()
    out_name = self.output_name_var.get().strip() or "icon.ico"

    if not out_dir:
      messagebox.showerror(APP_TITLE, "Output directory is required.")
      return

    enabled = [it for it in self.layers if it.enabled]
    if not enabled:
      messagebox.showerror(APP_TITLE, "No layers are enabled.")
      return

    # Sanity checks: paths exist + PNG decode + target sizes valid.
    bad: List[str] = []
    for it in enabled:
      tw, th = it.target_size
      if tw <= 0 or th <= 0:
        bad.append(f"{it.source_rel_path} (invalid target size {tw}x{th})")
        continue

      full = resolve_path(self.input_dir, it.source_rel_path)
      if not os.path.isfile(full):
        bad.append(f"{it.source_rel_path} (missing)")
        continue

      try:
        with Image.open(full) as img:
          img.verify()
      except Exception:
        bad.append(f"{it.source_rel_path} (invalid PNG)")

    if bad:
      messagebox.showerror(APP_TITLE, "Some enabled layers are invalid:\n\n" + "\n".join(bad))
      return

    try:
      ico_path = build_ico_from_layers(
        input_dir=self.input_dir,
        layers=self.layers,
        output_dir=out_dir,
        output_name=out_name,
      )
      self.persist_config_if_possible()
      messagebox.showinfo(APP_TITLE, f"ICO created:\n{ico_path}")
    except Exception as e:
      logging.exception("Build failed: %s", e)
      messagebox.showerror(APP_TITLE, f"Build failed:\n{e}")


# =============================================================================
# Entrypoint
# =============================================================================

def main() -> int:
  # Must happen early for best taskbar behavior on Windows.
  set_windows_app_user_model_id(APP_USER_MODEL_ID)

  app = App()
  app.mainloop()
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
