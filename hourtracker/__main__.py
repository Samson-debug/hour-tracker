"""`pythonw -m hourtracker` runs the tray app; `--popup` and `--manager <view>` open one window."""
import sys


def main():
    from . import core
    tray_mode = not any(a in sys.argv for a in ("--popup", "--remind", "--manager"))
    if tray_mode:
        core.startup_log("starting")
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
        sys.exit(1)


if __name__ == "__main__":
    main()
