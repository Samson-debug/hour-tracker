"""Builds the package you give to friends: dist/HourTracker-<version>.zip

    python build.py

It holds a folder with Hour Tracker.exe (no Python needed) and a README. None of your own data,
settings or secrets go into it: a friend's app starts empty, with the welcome screen.
If you saved a template sheet under Settings > Share with friends, the package offers it to them.
"""
import json, shutil, subprocess, sys, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from hourtracker import __version__, core
from hourtracker.tray import draw_icon

BUILD, DIST = ROOT / "build", ROOT / "dist"
NAME = "Hour Tracker"

README = """Hour Tracker {version}
=====================

Once an hour, a small window asks what you did. Answer in a few seconds;
over time you see where your days really go. Free, and your data stays yours.

INSTALL
1. Move this "Hour Tracker" folder somewhere you keep, for example Documents.
   (Not inside the zip, and not in Downloads if you clean that out.)
2. Double-click "Hour Tracker.exe".
3. Windows may say "Windows protected your PC". That is because the app isn't
   signed with a paid certificate. Click "More info", then "Run anyway". Once.
4. A short welcome screen sets you up. After that the app lives in the taskbar
   corner as a blue clock icon (click the ^ arrow if you don't see it).
   Right-click it for History, Reminders and Settings.

PHONE (optional)
Settings > Google Sheet walks you through connecting your own Google Sheet.
Then Settings > Phone gives you a QR code for the phone page, notifications
by email or Telegram, and reminders that ring on your phone through Google Calendar.

YOUR DATA
Everything is kept in %APPDATA%\\HourTracker on this PC (and in your own Google
Sheet, if you connect one). Nobody else, including whoever gave you the app, can see it.

UNINSTALL
Right-click the tray icon > Settings > untick "Start with Windows", then Quit.
Delete this folder. To also delete your data, delete %APPDATA%\\HourTracker.
"""


def main():
    for d in (BUILD, DIST / NAME):
        shutil.rmtree(d, ignore_errors=True)
    BUILD.mkdir(parents=True)

    # The app icon, from the same drawing as the tray icon.
    icon = BUILD / "icon.ico"
    draw_icon(False).resize((256, 256)).save(icon, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])

    # The template sheet friends copy, if you set one (never your own sheet, link or secret).
    share = ROOT / "hourtracker" / "share.json"
    template = core.copy_link(core.load_cfg().get("share_template", ""))
    share.write_text(json.dumps({"template_copy": template} if template else {}), encoding="utf-8")
    print("template sheet:", template or "none (friends will get the paste-the-script steps)")

    launcher = BUILD / "launch.py"
    launcher.write_text("from hourtracker.__main__ import main\nmain()\n", encoding="utf-8")
    data = [f"{f};hourtracker" for f in sorted((ROOT / "hourtracker").glob("*"))
            if f.suffix in (".html", ".css", ".js", ".gs") or f.name == "share.json"]
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onedir",
           "--name", NAME, "--icon", str(icon), "--distpath", str(DIST), "--workpath", str(BUILD / "work"),
           "--specpath", str(BUILD), "--paths", str(ROOT)]
    for d in data:
        cmd += ["--add-data", d]
    cmd.append(str(launcher))
    subprocess.run(cmd, check=True)
    share.unlink()

    folder = DIST / NAME
    (folder / "README.txt").write_text(README.format(version=__version__), encoding="utf-8")
    out = DIST / f"HourTracker-{__version__}.zip"
    out.unlink(missing_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in folder.rglob("*"):
            z.write(f, Path(NAME) / f.relative_to(folder))
    print(f"\nDone: {out}  ({out.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
