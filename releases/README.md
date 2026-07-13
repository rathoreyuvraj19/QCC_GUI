# QCC/QTRM Control GUI - Releases

Standalone executables for the QCC/QTRM Control desktop application.

## Linux

- **qcc_gui** (11 MB) - Standalone Linux executable with all dependencies bundled
  ```bash
  chmod +x qcc_gui
  ./qcc_gui
  ```
  No Python installation needed — just download and run.

## Windows

To build a Windows .exe:

1. On a Windows machine with Python 3.10+ installed:
   ```bash
   pip install -r requirements.txt
   pip install pyinstaller
   python build.py              # creates dist/qcc_gui/ folder
   python build.py --onefile    # creates single dist/qcc_gui.exe file
   ```

2. Copy the .exe to this folder and commit:
   ```bash
   cp dist/qcc_gui.exe releases/qcc_gui.exe
   git add releases/qcc_gui.exe
   git commit -m "Add Windows executable"
   git push
   ```

## Building from Source

```bash
pip install -r requirements.txt
python main.py
```

See [build.py](../build.py) and [.claude/skills/build-qcc-gui/](../.claude/skills/build-qcc-gui/SKILL.md)
for detailed build and distribution options.
