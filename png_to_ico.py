#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
png_to_ico_gui.py

Purpose
- Native GUI (CustomTkinter) for building a multi-layer Windows .ICO from PNG layers
  discovered strictly via filename size tokens like "000x000" (e.g., 128x128, 32x32).
- No default sizes. All layers come from:
  1) Automatic discovery in the selected input directory (regex size token), and/or
  2) Manual user configuration in the UI (selecting specific layer files).
- Layers are always ordered largest → smallest (by pixel area, then width/height).

Config behavior
- A `config.json` is stored INSIDE the selected input directory.
- When an input directory is selected:
  - If `config.json` exists, it is loaded and used to populate UI + selections.
  - If missing, the UI is populated from discovered layers and written to `config.json`.
- The program keeps `config.json` updated whenever the layer list or output settings change.

Dependencies
- pip install pillow icoutil customtkinter

Notes
- Layer filename MUST contain a "{W}x{H}" token somewhere in the basename.
  Example: "layer_128x128.png" or "icon-16x16.png"
- Manual layer add still requires the size token in the filename (per requirement).
- Paths in config are stored as relative to input directory when possible.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
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

APP_TITLE = "PNG to ICO (Layers via WxH filenames)"
CONFIG_FILENAME = "config.json"

# Match "000x000" style tokens anywhere in filename (1-4 digits each).
# Examples: "layer_128x128.png", "icon.16x16@2x.png", "foo_256x256_bar.png"
SIZE_TOKEN_RE = re.compile(r"(?P<w>\d{1,4})x(?P<h>\d{1,4})", re.IGNORECASE)


# =============================================================================
# Window
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

  # Windows: .ico
  try:
    if ico_abs and os.path.isfile(ico_abs):
      root.iconbitmap(ico_abs)
  except Exception:
    pass

  # Cross-platform: .png (Linux/macOS, sometimes Windows too)
  try:
    if png_abs and os.path.isfile(png_abs):
      img = tk.PhotoImage(file=png_abs)
      root.iconphoto(True, img)
      # Keep a reference so it doesn't get GC'd.
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
  - size: (width, height) parsed from filename token.
  - rel_path: file path relative to input_dir when possible.
  - enabled: whether to include this layer in ICO build.
  """
  size: Tuple[int, int]
  rel_path: str
  enabled: bool = True

  def size_key_desc(self) -> Tuple[int, int, int]:
    """
    Sort key for largest→smallest:
      - area desc
      - width desc
      - height desc
    """
    w, h = self.size
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
    # If relpath goes outside (..), store absolute to be explicit.
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

  This is intended to be called often to "keep config updated".
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
    w, h = it.size
    out.append({
      "size": f"{w}x{h}",
      "path": it.rel_path,
      "enabled": bool(it.enabled),
    })
  return out


def layers_from_config(data: dict) -> List[LayerItem]:
  """
  Deserialize layer items from config.json structure.
  """
  raw = data.get("layers", [])
  out: List[LayerItem] = []

  if not isinstance(raw, list):
    return out

  for entry in raw:
    if not isinstance(entry, dict):
      continue
    size_s = entry.get("size", "")
    p = entry.get("path", "")
    en = bool(entry.get("enabled", True))

    if not isinstance(size_s, str) or not isinstance(p, str):
      continue

    size = parse_size_token(size_s)
    if not size:
      # As a safety fallback, try to parse from the filename itself.
      size = parse_size_token(os.path.basename(p))
    if not size:
      continue

    out.append(LayerItem(size=size, rel_path=p, enabled=en))

  return out


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
    Users can override via the manual layer editor UI.
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
  - For newly discovered sizes not in config, add them enabled=True.
  - For config sizes whose files are missing, keep them (so user can fix),
    but they will be marked missing in UI and skipped at build-time if missing.

  Returns
  - merged list (not yet sorted).
  """
  out: List[LayerItem] = []
  seen_sizes: set = set()

  # Keep config items first.
  for it in cfg_layers:
    out.append(it)
    seen_sizes.add(it.size)

  # Append any discovered not in config.
  for size, relp in discovered.items():
    if size in seen_sizes:
      continue
    out.append(LayerItem(size=size, rel_path=relp, enabled=True))
    seen_sizes.add(size)

  # Normalize paths to prefer relative
  for it in out:
    it.rel_path = normalize_relpath(input_dir, resolve_path(input_dir, it.rel_path))

  return out


def sort_layers_desc(layers: List[LayerItem]) -> List[LayerItem]:
  """
  Always order largest → smallest.
  """
  return sorted(layers, key=lambda it: it.size_key_desc(), reverse=True)


# =============================================================================
# ICO build
# =============================================================================

def build_ico_from_layers(
  input_dir: str,
  layers: List[LayerItem],
  output_dir: str,
  output_name: str,
) -> str:
  """
  Build an .ICO from enabled layers in the list.

  Requirements enforced
  - Layers added in largest → smallest order.
  - Only enabled layers with existing files are included.
  - No resizing is done here; you are expected to provide correctly-sized PNGs.
    (If you want resizing back, say so and I’ll add an explicit toggle.)

  Returns
  - absolute path to the created ICO
  """
  if not output_name.lower().endswith(".ico"):
    output_name += ".ico"

  out_dir_abs = os.path.abspath(output_dir)
  os.makedirs(out_dir_abs, exist_ok=True)

  ico_path = os.path.abspath(os.path.join(out_dir_abs, output_name))

  enabled = [it for it in sort_layers_desc(layers) if it.enabled]

  # Validate: must have at least one existing layer.
  usable: List[Tuple[LayerItem, str]] = []
  for it in enabled:
    full = resolve_path(input_dir, it.rel_path)
    if os.path.isfile(full):
      usable.append((it, full))

  if not usable:
    raise ValueError("No enabled layer PNGs exist on disk. Fix paths or rescan layers.")

  # If duplicates sizes exist enabled, keep first (largest-first order still).
  seen_sizes: set = set()
  unique: List[Tuple[LayerItem, str]] = []
  for it, full in usable:
    if it.size in seen_sizes:
      continue
    unique.append((it, full))
    seen_sizes.add(it.size)

  ico = icoutil.IcoFile()

  # Add in descending order.
  for it, full in unique:
    ico.add_png(full)
    w, h = it.size
    logging.info("Add layer %dx%d: %s", w, h, full)

  ico.write(ico_path)
  logging.info("ICO written: %s", ico_path)

  return ico_path


# =============================================================================
# UI
# =============================================================================

class App(ctk.CTk):
  """
  CustomTkinter application for managing layers and building ICO files.

  UI features
  - Select input directory (contains layers + config.json).
  - Rescan layers from filenames containing WxH.
  - Manual layer controls:
    - Enable/disable per layer
    - Replace file per layer
    - Remove layer
    - Add new layer from file
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
    self.geometry("980x640")

    self.input_dir: str = ""
    self.layers: List[LayerItem] = []

    # Settings
    self.output_dir_var = tk.StringVar(value=os.path.abspath(os.getcwd()))
    self.output_name_var = tk.StringVar(value="icon.ico")

    # Top: Input directory controls
    self._build_top_controls()

    # Middle: Layers editor
    self._build_layers_editor()

    # Bottom: Actions + log
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

    self.btn_load = ctk.CTkButton(self.top, text="Load/Refresh", command=self.on_load_input_dir, width=110)
    self.btn_load.grid(row=0, column=3, padx=8, pady=8)

    self.btn_rescan = ctk.CTkButton(self.top, text="Rescan Layers", command=self.on_rescan_layers, width=130)
    self.btn_rescan.grid(row=0, column=4, padx=8, pady=8)

    self.top.grid_columnconfigure(1, weight=1)

  def _build_layers_editor(self) -> None:
    self.mid = ctk.CTkFrame(self)
    self.mid.pack(fill="both", expand=True, padx=12, pady=6)

    header = ctk.CTkFrame(self.mid)
    header.pack(fill="x", padx=8, pady=(8, 6))

    ctk.CTkLabel(header, text="Layers (largest → smallest) — filenames must contain WxH token").pack(side="left", padx=8)

    self.btn_add_layer = ctk.CTkButton(header, text="Add Layer…", command=self.on_add_layer, width=120)
    self.btn_add_layer.pack(side="right", padx=8)

    # Scrollable list
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

    # Save config whenever output fields change
    self.output_dir_var.trace_add("write", lambda *_: self.persist_config_if_possible())
    self.output_name_var.trace_add("write", lambda *_: self.persist_config_if_possible())

  # ---------------------------------------------------------------------------
  # Config IO
  # ---------------------------------------------------------------------------

  def make_config_dict(self) -> dict:
    return {
      "version": 1,
      "input_dir": ".",  # implicit; config lives inside input_dir
      "output_dir": self.output_dir_var.get(),
      "output_name": self.output_name_var.get(),
      "layers": layers_to_config(sort_layers_desc(self.layers)),
    }

  def persist_config_if_possible(self) -> None:
    """
    Save config.json into the current input directory, if one is selected.
    """
    if not self.input_dir or not os.path.isdir(self.input_dir):
      return
    save_config(self.input_dir, self.make_config_dict())

  def load_or_init_from_input_dir(self, input_dir: str) -> None:
    """
    When selecting an input dir:
    - Load config.json if present
    - Else discover layers and create config

    Output dir default rule:
    - If config has no output_dir (or blank), default output_dir to input_dir.
    - If no config exists, output_dir defaults to input_dir.
    """
    self.input_dir = os.path.abspath(input_dir)
    self.input_dir_var.set(self.input_dir)

    cfg = load_config(self.input_dir)
    discovered = discover_layers_in_dir(self.input_dir)

    # Default output dir to the selected input directory unless config explicitly provides one.
    default_out_dir = self.input_dir

    if cfg:
      cfg_layers = layers_from_config(cfg)
      merged = merge_config_layers_with_discovery(self.input_dir, cfg_layers, discovered)

      # Restore output settings if present; otherwise default to input dir
      out_dir = cfg.get("output_dir")
      out_name = cfg.get("output_name")

      if isinstance(out_dir, str) and out_dir.strip():
        self.output_dir_var.set(out_dir)
      else:
        self.output_dir_var.set(default_out_dir)

      if isinstance(out_name, str) and out_name.strip():
        self.output_name_var.set(out_name)
      # else: keep whatever is already in the UI (typically "icon.ico")

      self.layers = sort_layers_desc(merged)

    else:
      # No config -> initialize from discovered layers
      init_layers: List[LayerItem] = []
      for size, relp in discovered.items():
        init_layers.append(LayerItem(size=size, rel_path=relp, enabled=True))
      self.layers = sort_layers_desc(init_layers)

      # No config means new directory: default output dir to input dir
      self.output_dir_var.set(default_out_dir)

    # Always persist (keeps config updated + creates if missing)
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

      w, h = it.size
      ctk.CTkLabel(frame, text=f"{w}x{h}", width=90).grid(row=0, column=1, padx=6, pady=8, sticky="w")

      # Path + missing indicator
      full = resolve_path(self.input_dir, it.rel_path) if self.input_dir else it.rel_path
      missing = (self.input_dir and not os.path.isfile(full))

      path_text = it.rel_path
      if missing:
        path_text += "  (MISSING)"

      lbl = ctk.CTkLabel(frame, text=path_text, anchor="w")
      lbl.grid(row=0, column=2, padx=6, pady=8, sticky="ew")

      def _make_on_replace(i: int):
        def _on_replace():
          self.on_replace_layer_file(i)
        return _on_replace

      def _make_on_remove(i: int):
        def _on_remove():
          self.on_remove_layer(i)
        return _on_remove

      btn_replace = ctk.CTkButton(frame, text="Replace…", command=_make_on_replace(idx), width=110)
      btn_replace.grid(row=0, column=3, padx=6, pady=8)

      btn_remove = ctk.CTkButton(frame, text="Remove", command=_make_on_remove(idx), width=90)
      btn_remove.grid(row=0, column=4, padx=(6, 8), pady=8)

      frame.grid_columnconfigure(2, weight=1)

      self._layer_rows.append({
        "frame": frame,
        "enabled_var": enabled_var,
      })

  # ---------------------------------------------------------------------------
  # UI callbacks
  # ---------------------------------------------------------------------------

  def on_browse_input_dir(self) -> None:
    p = filedialog.askdirectory(title="Select input directory (contains layers + config.json)")
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

    Requirement enforced:
    - Filename must contain a WxH token. Otherwise reject.
    """
    if not self.input_dir or not os.path.isdir(self.input_dir):
      messagebox.showerror(APP_TITLE, "Select a valid input directory first.")
      return

    p = filedialog.askopenfilename(
      title="Select layer PNG (filename must contain WxH token)",
      initialdir=self.input_dir,
      filetypes=[("PNG images", "*.png"), ("All files", "*.*")],
    )
    if not p:
      return

    size = parse_size_token(os.path.basename(p))
    if not size:
      messagebox.showerror(APP_TITLE, "That filename does not contain a WxH token (e.g., 128x128). Rename the file first.")
      return

    # Prefer keeping layer files inside the input dir (config locality).
    p_abs = os.path.abspath(p)
    in_abs = os.path.abspath(self.input_dir)
    try:
      rel = os.path.relpath(p_abs, in_abs)
      if rel.startswith(".."):
        messagebox.showerror(APP_TITLE, "Layer file must be inside the selected input directory.")
        return
    except Exception:
      messagebox.showerror(APP_TITLE, "Failed to validate layer path; ensure it is inside the input directory.")
      return

    rel_path = normalize_relpath(self.input_dir, p_abs)

    # If size already exists, add another entry (allowed), but it will be de-duped at build-time.
    self.layers.append(LayerItem(size=size, rel_path=rel_path, enabled=True))
    self.layers = sort_layers_desc(self.layers)

    self.persist_config_if_possible()
    self.refresh_layer_rows()

  def on_replace_layer_file(self, idx: int) -> None:
    if not self.input_dir or not os.path.isdir(self.input_dir):
      messagebox.showerror(APP_TITLE, "Select a valid input directory first.")
      return

    current = self.layers[idx]
    p = filedialog.askopenfilename(
      title="Select replacement layer PNG (filename must contain WxH token)",
      initialdir=self.input_dir,
      filetypes=[("PNG images", "*.png"), ("All files", "*.*")],
    )
    if not p:
      return

    size = parse_size_token(os.path.basename(p))
    if not size:
      messagebox.showerror(APP_TITLE, "That filename does not contain a WxH token (e.g., 128x128). Rename the file first.")
      return

    p_abs = os.path.abspath(p)
    in_abs = os.path.abspath(self.input_dir)
    try:
      rel = os.path.relpath(p_abs, in_abs)
      if rel.startswith(".."):
        messagebox.showerror(APP_TITLE, "Replacement file must be inside the selected input directory.")
        return
    except Exception:
      messagebox.showerror(APP_TITLE, "Failed to validate layer path; ensure it is inside the input directory.")
      return

    current.size = size
    current.rel_path = normalize_relpath(self.input_dir, p_abs)

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

    # Validate that enabled files exist and are valid PNGs (light check).
    enabled = [it for it in self.layers if it.enabled]
    if not enabled:
      messagebox.showerror(APP_TITLE, "No layers are enabled.")
      return

    # Optional: verify PNG decode for enabled layers (fast sanity).
    bad: List[str] = []
    for it in enabled:
      full = resolve_path(self.input_dir, it.rel_path)
      if not os.path.isfile(full):
        bad.append(f"{it.rel_path} (missing)")
        continue
      try:
        with Image.open(full) as img:
          img.verify()
      except Exception:
        bad.append(f"{it.rel_path} (invalid PNG)")

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
