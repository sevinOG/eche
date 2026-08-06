# eche_source — edit & build

## The only script you need

```bat
BUILD.bat
```

Creates/refreshes the portable app in **`../eche/`**.

## First setup

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
BUILD.bat
```

## Dev run (no freeze)

```bat
.venv\Scripts\python.exe eche_app.py
```
