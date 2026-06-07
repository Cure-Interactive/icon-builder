# Icon Builder Wiki

Icon Builder builds a Windows `.ico` file from PNG and SVG layers. It can also render a PNG from the same source layers for apps that use a window or splash image.

## Running

```bash
python icon-builder.py
```

## Installation

```bash
python setup.py --venv
```

Or install dependencies manually from `requirements.txt`, then run:

```bash
python icon-builder.py
```

## Layer Discovery

The app detects PNG and SVG files whose names include a size token:

```text
icon_256x256.svg
icon_128x128.png
icon_32x32.svg
icon_16x16.png
```

Layers are ordered largest to smallest in the generated `.ico`.

## Configuration

The app saves layer configuration into `icon-builder.json` inside the selected input directory. If a legacy `png-to-ico.json` exists and `icon-builder.json` does not, the app loads the legacy config and then saves the current config as `icon-builder.json`.

PNG output is configured in the `png_output` object:

```json
{
  "png_output": {
    "enabled": true,
    "output_dir": "..",
    "output_name": "icon.png",
    "size": 256
  }
}
```

App-level recent-folder settings are stored in `config.json` beside `icon-builder.py`. Runtime config is ignored by Git.

## Workflow

1. Start `icon-builder.py`.
2. Select the input directory containing PNG or SVG layers.
3. Review detected layers.
4. Add or replace layer files as needed.
5. Adjust target sizes if needed.
6. Build the `.ico` file and optional `.png` file.

## SVG Notes

SVG sources are rasterized with `resvg-py` at each layer's target size before being added to the `.ico`. SVG files that rely on unavailable system fonts may render differently across operating systems; for the most consistent icon output, convert text to paths before building.
