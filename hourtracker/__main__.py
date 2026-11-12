"""`pythonw -m hourtracker` runs the tray app; `--popup` and `--manager <view>` open one window."""
import sys


def main():
    from . import core
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
    except Exception:
        core.log_error()
        sys.exit(1)


if __name__ == "__main__":
    main()
