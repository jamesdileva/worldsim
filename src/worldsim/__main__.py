from .cli import main

sys_exit = main

if __name__ == "__main__":
    raise SystemExit(main())
