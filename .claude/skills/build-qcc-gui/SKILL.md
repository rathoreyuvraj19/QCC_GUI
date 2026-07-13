---
name: build-qcc-gui
description: Package the QCC/QTRM Control GUI as a standalone Windows .exe with all dependencies bundled. Use when asked to build, package, or create an exe of the app.
---

# Build QCC/QTRM Control GUI to Standalone .exe

Packages the PySide6 desktop app into a standalone Windows .exe with all
required dependencies (PySide6, matplotlib, openpyxl, etc.) bundled, so
end users don't need to install Python or run pip.

## Prerequisites

PyInstaller must be installed:

```bash
pip install pyinstaller
```

## Building

### Default: Directory package (faster to run, slower to distribute)

```bash
python build.py
```

Produces `dist/qcc_gui/` folder containing:
- `qcc_gui.exe` - the application
- All bundled libraries and dependencies
- Total size ~300–400 MB

Users copy the entire `qcc_gui/` folder to their machine and run the .exe.

### Single-file exe (slower to run, easier to distribute)

```bash
python build.py --onefile
```

Produces `dist/qcc_gui.exe` - a single file.

On first run it unpacks to a temp folder (~300–500 MB), so first
startup is slower (3–5 sec). After that it caches and runs normally.
Users just copy this one .exe file to their machine.

## Outputs

After building, check:

```bash
ls -lh dist/qcc_gui/qcc_gui.exe       # (--onedir, default)
# or
ls -lh dist/qcc_gui.exe               # (--onefile)
```

A build info file `dist/README_BUILD.txt` is also created with post-build
steps for distribution.

## What's included

- All source code: `main.py`, `core/`, `tabs/`, `widgets/`, `apps/`
- PySide6 ≥6.11 runtime
- openpyxl ≥3.1 (Excel file support)
- matplotlib ≥3.6 (burn-test plot rendering)
- All stdlib dependencies

## What's excluded (to keep size small)

- Tests and test fixtures
- Documentation (README.md, CLAUDE.md, docs/)
- Git history (.git/)
- Build artifacts (build/, __pycache__/, .pyc files)
- Development tools

## Troubleshooting

**"PyInstaller not found"**: `pip install pyinstaller`

**"qcc_gui.exe won't run"**: Check that all dependencies listed in
`requirements.txt` are installed in your build environment:
`pip install -r requirements.txt`

**"App is very large (>500 MB)"**: This is normal for a bundled PySide6
app with matplotlib. The single-file mode (`--onefile`) can grow even
larger due to compression overhead. Use the directory mode for end-user
distribution.

**"Windows antivirus flags the .exe"**: Standalone exes built with
PyInstaller sometimes trigger heuristic scanning because they unpack DLLs
to temp at runtime. This is false-positive; the code is not modified.
Consider code-signing the .exe for production use.
