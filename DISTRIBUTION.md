# QCC/QTRM Control GUI - Standalone Distribution

This guide explains how to build and distribute the QCC/QTRM Control GUI as a complete standalone Windows executable that requires **no Python, no dependencies, and no internet connection** to run.

## What You Have

✅ **Complete standalone .exe**: `dist/qcc_gui/qcc_gui.exe` (10.77 MB)  
✅ **All dependencies bundled**: PySide6, matplotlib, openpyxl, and all system libraries  
✅ **Total package size**: ~205 MB (dist/qcc_gui/ folder)  
✅ **Ready for offline use**: Works on any Windows machine with no setup required

## Building the .exe

### Prerequisites (build machine only)

You only need these on the machine where you **build** the exe, not on machines where users run it:

```bash
pip install pyinstaller
pip install -r requirements.txt
```

### Build Command

Two options:

**Option 1: Directory package (RECOMMENDED)** — faster to run, easier to distribute

```bash
python build.py
```

Produces:
- `dist/qcc_gui/` folder (~205 MB)
- `dist/qcc_gui/qcc_gui.exe` (the app)
- `dist/qcc_gui/_internal/` (all bundled libraries)

**Option 2: Single-file exe** — simplest to copy, slower first startup

```bash
python build.py --onefile
```

Produces:
- `dist/qcc_gui.exe` (single file ~300-400 MB)
- Unpacks to temp folder on first run (3-5 sec slower)
- Runs normally after first startup (cached)

**Recommended for end users**: Option 1 (directory) — it's faster and can be run directly.

## Distributing to Users

### Option 1: Zip the folder (Recommended)

1. Navigate to the repo's `dist/` folder
2. Zip the entire `qcc_gui/` folder:
   ```
   qcc_gui.zip (~205 MB)
   ```
3. Send users the .zip file
4. Users extract and run:
   ```
   qcc_gui/qcc_gui.exe
   ```

### Option 2: Single-file exe

1. Copy `dist/qcc_gui.exe` from a `--onefile` build
2. Send the .exe file directly to users
3. Users run it directly:
   ```
   qcc_gui.exe
   ```

### Option 3: USB drive or offline media

Since the exe is completely standalone:
- Copy `dist/qcc_gui/` folder to USB drive
- Users can run it on any Windows machine without internet
- Works on offline PCs with no external dependencies

## What's Included

✅ **Full application**:
- Main window and all tabs (Mode, Status, Timing, Link Test, Memory, etc.)
- UDP sender/receiver (QThread-based, non-blocking)
- Data logging to CSV with burn-test support
- Plot analysis for logged data
- All 96 QTRM slot editing

✅ **Bundled libraries**:
- PySide6 6.11+ (Qt framework)
- matplotlib 3.6+ (plotting)
- openpyxl 3.1+ (Excel export)
- numpy and all dependencies

✅ **Data files**:
- Application code and modules
- UI resources

❌ **Excluded** (to keep size small):
- Tests and test fixtures
- Documentation (README.md, CLAUDE.md)
- Git history (.git/)
- Development tools

## System Requirements (End Users)

| Requirement | Minimum | Recommended |
|---|---|---|
| OS | Windows 7 SP1 | Windows 10 / 11 |
| Architecture | x86-64 | x86-64 |
| RAM | 512 MB | 2 GB+ |
| Disk space | 250 MB free | 500 MB free |
| Python | **None required** | N/A |
| Administrator | No | No |

## Running on Offline PC

1. Copy `qcc_gui/` folder or `qcc_gui.exe` to the target machine
2. No installation needed
3. No Python required
4. No internet connection needed
5. Run `qcc_gui.exe` directly

The application will work completely offline — no cloud calls, no license checking, no external dependencies.

## First Run

On first run, the exe will:
1. Extract bundled libraries to Windows temp folder (automatic)
2. Initialize the GUI
3. Display the main window with all tabs

This takes 2-3 seconds on the first launch, then runs at normal speed on subsequent launches (libraries are cached).

## Antivirus Notes

Some Windows antivirus software may flag the .exe on first run because:
- PyInstaller unpacks DLLs to temp at runtime (normal behavior)
- Heuristic scanning detects this unpacking as suspicious

This is a **false positive**. The code is not modified; it's a standard PyInstaller pattern.

**To prevent flagging**:
- Code-sign the .exe (requires a signing certificate)
- Add exception to antivirus allow-list
- Users can safely ignore the warning

## Troubleshooting

### "The application failed to start"
- Ensure Windows 7 SP1 or later (exe needs recent Windows APIs)
- Try running as Administrator
- Check Windows Update for latest patches

### "App runs slowly on first launch"
- This is normal — DLLs are being extracted to temp (~2-3 sec)
- Subsequent runs are normal speed (cached)

### "Antivirus blocks the exe"
- Safe to ignore — it's a false positive (PyInstaller's unpacking behavior)
- Add to antivirus allow-list if it blocks every time
- Alternatively, code-sign the exe for your organization

### "App window appears but doesn't respond"
- Wait 5-10 seconds for Qt to fully initialize
- Check Windows Event Viewer for errors
- Try running as Administrator

## Rebuilding After Code Changes

If you modify the Python code:

```bash
python build.py
```

This regenerates:
- `dist/qcc_gui.exe` (new version)
- `dist/qcc_gui/_internal/` (updated libraries)
- `dist/README_BUILD.txt` (build info)

Then re-zip `dist/qcc_gui/` and redistribute.

## Development vs. Distribution

| Scenario | Command | When |
|---|---|---|
| Testing changes | `python main.py` | During development |
| Final build | `python build.py` | Before distribution |
| Single-file exe | `python build.py --onefile` | Maximum portability |

## Build Artifacts

After building, you'll see:

```
dist/
  qcc_gui.exe           (executable — runs as part of folder package)
  _internal/            (all bundled DLLs and libraries)
  README_BUILD.txt      (auto-generated build info)

build/                  (intermediate PyInstaller files — can delete)
qcc_gui.spec            (PyInstaller spec file — can delete)
```

You can safely delete `build/` and `qcc_gui.spec` after building — they're rebuild artifacts.

## Quick Start Checklist

- [ ] Install PyInstaller: `pip install pyinstaller`
- [ ] Install dependencies: `pip install -r requirements.txt`
- [ ] Build exe: `python build.py`
- [ ] Verify output: `dist/qcc_gui/qcc_gui.exe` exists
- [ ] Test on target machine (if available)
- [ ] Zip `dist/qcc_gui/` for distribution
- [ ] Send zip to users or place on USB drive

## Support

If the .exe doesn't run on an end-user machine:

1. Verify Windows 7 SP1 or later is installed
2. Check that the entire `qcc_gui/` folder is present (don't delete `_internal/`)
3. Try running as Administrator
4. Provide the user with `dist/README_BUILD.txt` for reference

## See Also

- [CLAUDE.md](CLAUDE.md) — Development notes and open issues
- [README.md](README.md) — Full application documentation
- [build.py](build.py) — Build script source (customizable if needed)
