from pathlib import Path

def print_py_files(directory: str):
    base = Path(directory)

    ignore_dirs = ["__pycache__", ".venv",  "visions"]

    with open("pokertable_files.txt", "w", encoding="utf-8") as out:
        for py_file in sorted(base.rglob("*.py")):
            if any(ignore_dir in py_file.parts for ignore_dir in ignore_dirs):
                continue

            out.write(f"{py_file.relative_to(base)}:\n")

            with py_file.open(encoding="utf-8") as f:
                out.write(f.read())

            out.write("\n\n")

if __name__ == "__main__":
    print_py_files("../app/")
