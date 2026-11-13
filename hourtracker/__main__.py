"""`pythonw -m hourtracker` runs the tray app; `--popup` and `--manager <view>` open one window."""
import sys


def main():
    from . import core
    mode = next((a for a in ("--popup", "--remind", "--manager") if a in sys.argv), None)
    tray_mode = mode is None
    if tray_mode:
        core.startup_log("starting")
    try:
        removed = core.unblock_self()
        if removed:
            core.startup_log(f"removed the downloaded-file mark from {removed} app files")
    except Exception:
        core.log_error()
    if tray_mode:
        try:   # a hard crash (not a Python error) still leaves its trace in crash.log
            import faulthandler
            faulthandler.enable(open(core.data_dir() / "crash.log", "a", encoding="utf-8"))
        except Exception:
            pass
    try:
        if "--popup" in sys.argv:           # the tray process never loads the browser engine
            from . import popup
            popup.run()
        elif "--remind" in sys.argv:
            from . import remind
            remind.run()
        elif "--manager" in sys.argv:
            from . import manager
            manager.run(sys.argv[-1])
        else:
            from . import tray
            tray.run()
    except Exception as e:
        core.log_error()
        if tray_mode:
            core.startup_log(f"stopped by an error: {e!r} (details in hourtracker.log)")
        else:
            core.window_failed(mode)
        sys.exit(1)


if __name__ == "__main__":
    main()
