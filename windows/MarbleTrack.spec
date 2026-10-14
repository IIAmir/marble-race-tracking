# PyInstaller spec for MarbleTrack.exe (one-folder build: starts fast, fewer antivirus false alarms).
#   pyinstaller --noconfirm windows/MarbleTrack.spec
import os
from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
hidden = (
    collect_submodules("websockets")
    + collect_submodules("marbletrack")
    + collect_submodules("marbletrack_app")
)
optional = ["webview", "webview.platforms.edgechromium", "webview.platforms.winforms", "clr_loader", "pythonnet",
            "pygrabber.dshow_graph", "cv2_enumerate_cameras", "pypylon.pylon"]
for mod in optional:  # bundled when installed on the build machine
    try:
        __import__(mod)
        hidden.append(mod)
    except Exception:
        pass

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    pathex=[ROOT],
    datas=[(os.path.join(ROOT, "marbletrack_app", "web"), os.path.join("marbletrack_app", "web"))],
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "PyQt5", "PyQt6", "PySide2", "PySide6", "IPython", "pytest", "hypothesis"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="MarbleTrack",
    icon=os.path.join(SPECPATH, "marbletrack.ico"),
    version=os.path.join(SPECPATH, "version_info.txt"),
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="MarbleTrack", upx=False)
