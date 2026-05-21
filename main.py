import sys, os, json
from pathlib import Path

if not getattr(sys, 'frozen', False):
    def check_and_activate_venv():
        script_dir = Path(__file__).parent.resolve()
        venv_names = ['venv', '.venv', 'env', '.env']

        for venv_name in venv_names:
            venv_path = script_dir / venv_name

            if sys.platform == "win32":
                python_exe = venv_path / "Scripts" / "python.exe"
            else:
                python_exe = venv_path / "bin" / "python"

            if venv_path.exists() and python_exe.exists():
                if sys.executable != str(python_exe):
                    print(f"Found virtual environment: {venv_name}")
                    print(f"Restarting with venv Python: {python_exe}")
                    os.execv(str(python_exe), [str(python_exe)] + sys.argv)
                else:
                    print(f"Already running in virtual environment: {venv_name}")
                return True

        print("No virtual environment found, using global Python")
        return False

    check_and_activate_venv()

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from intern.cli import main
from intern.utils import resolve_config_path, parse_config_json

if __name__ == "__main__":
    try:
        if '--fetch' in sys.argv:
            config_args = [a for a in sys.argv[1:] if not a.startswith('-')]
            if not config_args:
                print(json.dumps({"error": "No config path provided"}))
                sys.exit(1)
            try:
                resolved = resolve_config_path(config_args[0])
                if not resolved:
                    print(json.dumps({"error": f"Config not found: {config_args[0]}"}))
                    sys.exit(1)
                config = parse_config_json(resolved)
                print(json.dumps({
                    "model": list(config.get("model", {}).keys()),
                    "data":  list(config.get("data",  {}).keys()),
                }))
            except Exception as e:
                print(json.dumps({"error": str(e)}))
                sys.exit(1)
            sys.exit(0)

        main()
    except KeyboardInterrupt:
        print("\n\nOperation cancelled by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nFATAL ERROR: {e}")
        sys.exit(1)