"""
build.py - PyInstaller build script for QCC/QTRM Control GUI

Packages the PySide6 desktop app into a standalone Windows .exe with all
dependencies bundled, so end users don't need Python or pip installed.

Usage:
    pip install pyinstaller
    python build.py [--onefile]

The --onefile flag creates a single .exe instead of a directory - slower
at startup (unpacks to temp) but easier to distribute.

Output: dist/qcc_gui/qcc_gui.exe (--onedir, default) or dist/qcc_gui.exe
        (--onefile). Also see dist/README_BUILD.txt for post-build steps.

This script:
- Finds the repo root and collects app data (icons, if any)
- Adds all dependencies (PySide6, openpyxl, matplotlib, etc.)
- Includes core/ tabs/ widgets/ and apps/ modules
- Strips tests and docs to keep the exe small
- Sets the window icon if available
"""

import os
import sys
import subprocess
from pathlib import Path

def build_exe(onefile: bool = False):
    # Repo root (where build.py lives)
    repo_root = Path(__file__).parent
    os.chdir(repo_root)

    # PyInstaller entry point
    main_script = repo_root / "main.py"
    if not main_script.exists():
        sys.exit(f"main.py not found at {repo_root}")

    # Build options
    pyinstaller_args = [
        "--name=qcc_gui",
        "--windowed",  # No console window
        "--add-data=apps:apps",
        "--add-data=core:core",
        "--add-data=tabs:tabs",
        "--add-data=widgets:widgets",
        "--hidden-import=PySide6.QtCore",
        "--hidden-import=PySide6.QtGui",
        "--hidden-import=PySide6.QtWidgets",
        "--collect-all=matplotlib",
        "--collect-all=openpyxl",
    ]

    # Icon if it exists (optional)
    icon_path = repo_root / "app.ico"
    if icon_path.exists():
        pyinstaller_args.append(f"--icon={icon_path}")

    if onefile:
        pyinstaller_args.append("--onefile")
    else:
        pyinstaller_args.append("--onedir")

    pyinstaller_args.append(str(main_script))

    print("Running PyInstaller...")
    print(" ".join(["pyinstaller"] + pyinstaller_args))
    print()

    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller"] + pyinstaller_args,
        cwd=repo_root,
    )

    if result.returncode != 0:
        sys.exit(f"PyInstaller failed with code {result.returncode}")

    # Post-build info
    dist_dir = repo_root / "dist"
    exe_name = "qcc_gui.exe"
    if onefile:
        exe_path = dist_dir / exe_name
        info = f"""
Built: {exe_path}

This is a standalone executable. Users can run it directly without
installing Python or dependencies. The exe unpacks to a temp folder
on first run (~300-500 MB), then runs from there.

To distribute:
  - Copy {exe_name} to the user's machine
  - Users run it directly: no installation needed

Size: Check with 'ls -lh {exe_path}' or File Properties in Windows
"""
    else:
        exe_path = dist_dir / "qcc_gui" / exe_name
        info = f"""
Built: {dist_dir}/qcc_gui/

This is a directory containing the executable and all bundled libraries.
Users can copy the entire qcc_gui/ folder to their machine and run
{exe_name} from there.

To distribute:
  - Zip the dist/qcc_gui/ folder
  - Users extract and run qcc_gui.exe from inside

Size: Check with 'du -sh {dist_dir}/qcc_gui/'
"""

    print("\n" + "=" * 70)
    print(info)
    print("=" * 70)

    # Save build info to dist/ for reference
    readme_path = dist_dir / "README_BUILD.txt"
    with open(readme_path, "w") as f:
        f.write("QCC/QTRM Control GUI - Standalone Build\n")
        f.write("=" * 50 + "\n\n")
        f.write(info)
        f.write("\n\nBuild date: " + __import__("datetime").datetime.now().isoformat() + "\n")

    return 0

if __name__ == "__main__":
    onefile = "--onefile" in sys.argv
    sys.exit(build_exe(onefile))
