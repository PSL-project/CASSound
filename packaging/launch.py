import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from cassound.cli import main
    raise SystemExit(main())
