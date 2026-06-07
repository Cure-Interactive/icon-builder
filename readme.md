# Icon Builder

Desktop utility for building Windows `.ico` files from one or more PNG or SVG layers. It can also render a project PNG from the same source layers.

## Requirements

- Python 3.10+
- Dependencies from `requirements.txt`

## Install

```bash
python setup.py --venv
```

Or manually:

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

On Linux or macOS, activate the virtual environment with `source .venv/bin/activate`.

## Run

```bash
python icon-builder.py
```

## Input Convention

The app can discover PNG and SVG layers whose filenames include a size token such as `256x256`, `64x64`, or `16x16`.

See `wiki.md` for details.
