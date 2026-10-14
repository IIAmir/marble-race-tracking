"""PyInstaller entry point for MarbleTrack.exe."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()  # camera processes re-enter the frozen exe; must run first
    from marbletrack_app.main import main

    sys.exit(main())
