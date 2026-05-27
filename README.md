# PNG To ICO

Desktop utility for building Windows `.ico` files from one or more PNG layers.

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
python png_to_ico.py
```

## Input Convention

The app can discover PNG layers whose filenames include a size token such as `256x256`, `64x64`, or `16x16`.

See `wiki.md` for details.
