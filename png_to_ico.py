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
- A `config.json` is stored INSIDE the selected input directory.
- When an input directory is selected:
  - If `config.json` exists, it is loaded and used to populate UI + selections.
  - If missing, the UI is populated from discovered layers and written to `config.json`.
- The program keeps `config.json` updated whenever:
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

LOG_FILENAME = "png_to_ico.log"

logging.basicConfig(
  level=logging.INFO,
  format="%(asctime)s [%(levelname)s] %(message)s",
  handlers=[
    logging.FileHandler(LOG_FILENAME, encoding="utf-8"),
    logging.StreamHandler(sys.stdout),
  ],
)

logging.getLogger("PIL").setLevel(logging.WARNING)


# =============================================================================
# Constants / Regex
# =============================================================================

APP_TITLE = "PNG to ICO (Manual Layers + Target Sizes)"
CONFIG_FILENAME = "config.json"

# Match "000x000" style tokens anywhere in filename (1-4 digits each).
SIZE_TOKEN_RE = re.compile(r"(?P<w>\d{1,4})x(?P<h>\d{1,4})", re.IGNORECASE)


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
  Get config.json full path inside input directory.
  """
  return os.path.join(os.path.abspath(input_dir), CONFIG_FILENAME)


def load_config(input_dir: str) -> Optional[dict]:
  """
  Load config.json from input directory, if it exists.

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
  Save config.json in the input directory.
  Intended to be called often to "keep config updated".
  """
  p = config_path_for_input_dir(input_dir)
  try:
    with open(p, "w", encoding="utf-8") as f:
      json.dump(data, f, indent=2)
  except Exception as e:
    logging.warning("Failed to save config: %s (%s)", p, e)


def layers_to_config(layers: List[LayerItem]) -> List[dict]:
  """
  Serialize layer items for config.json.
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
  Deserialize layer items from config.json structure.

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
  - Select input directory (contains config.json, may contain discovered layers).
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

    set_window_icon(self, "icon.ico", "icon.png")

    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")

    self.title(APP_TITLE)
    self.geometry("1040x680")

    self.input_dir: str = ""
    self.layers: List[LayerItem] = []

    self.output_dir_var = tk.StringVar(value=os.path.abspath(os.getcwd()))
    self.output_name_var = tk.StringVar(value="icon.ico")

    self._build_top_controls()
    self._build_layers_editor()
    self._build_bottom_controls()

  # ---------------------------------------------------------------------------
  # UI construction
  # ---------------------------------------------------------------------------

  def _build_top_controls(self) -> None:
    self.top = ctk.CTkFrame(self)
    self.top.pack(fill="x", padx=12, pady=(12, 6))

    self.input_dir_var = tk.StringVar(value="")

    ctk.CTkLabel(self.top, text="Input Directory (layers + config.json):").grid(row=0, column=0, sticky="w", padx=8, pady=8)
    self.input_entry = ctk.CTkEntry(self.top, textvariable=self.input_dir_var)
    self.input_entry.grid(row=0, column=1, sticky="ew", padx=8, pady=8)

    self.btn_browse_input = ctk.CTkButton(self.top, text="Browse…", command=self.on_browse_input_dir, width=110)
    self.btn_browse_input.grid(row=0, column=2, padx=8, pady=8)

    self.btn_load = ctk.CTkButton(self.top, text="Load/Refresh", command=self.on_load_input_dir, width=120)
    self.btn_load.grid(row=0, column=3, padx=8, pady=8)

    self.btn_rescan = ctk.CTkButton(self.top, text="Rescan Layers", command=self.on_rescan_layers, width=130)
    self.btn_rescan.grid(row=0, column=4, padx=8, pady=8)

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
    self.out_name_entry = ctk.CTkEntry(self.bottom, textvariable=self.output_name_var, width=220)
    self.out_name_entry.grid(row=1, column=1, sticky="w", padx=8, pady=8)

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

      if isinstance(out_dir, str) and out_dir.strip():
        self.output_dir_var.set(out_dir)
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

      # Path + missing indicator
      full = resolve_path(self.input_dir, it.source_rel_path) if self.input_dir else it.source_rel_path
      missing = (self.input_dir and not os.path.isfile(full))

      path_text = it.source_rel_path
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

  def on_browse_input_dir(self) -> None:
    p = filedialog.askdirectory(title="Select input directory (contains config.json)")
    if not p:
      return
    self.input_dir_var.set(os.path.abspath(p))
    self.on_load_input_dir()

  def on_load_input_dir(self) -> None:
    p = self.input_dir_var.get().strip()
    if not p or not os.path.isdir(p):
      messagebox.showerror(APP_TITLE, "Input directory is missing or invalid.")
      return
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
    p = filedialog.askdirectory(title="Select output directory")
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
  app = App()
  app.mainloop()
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
