## Before You Start

Please read `pyproject.toml` first.

---

## Python

```toml
requires-python = ">=3.12"
```

---

## Poetry

Documentation:  
[https://python-poetry.org/docs/](https://python-poetry.org/docs/)

### Install Poetry

**Linux, macOS, Windows (WSL)**

```bash
curl -sSL https://install.python-poetry.org | python3 -
```

---

### Add Poetry to Your `PATH`

The installer creates a `poetry` executable in a platform-specific directory:

- `$HOME/.local/bin` on Unix-like systems
    
- `%APPDATA%\Python\Scripts` on Windows
    
- `$POETRY_HOME/bin` if `$POETRY_HOME` is set
    

If this directory is **not in your `PATH`**, **add it** manually so you can run Poetry using the `poetry` command.

After installation, verify it with:

```bash
poetry --version
```

---

## Install Dependencies

Reference:  
[https://python-poetry.org/docs/basic-usage/#initialising-a-pre-existing-project](https://python-poetry.org/docs/basic-usage/#initialising-a-pre-existing-project)

Working directory: `~/service`

```bash
poetry config virtualenvs.in-project true
poetry env use python3.12
poetry install
```

This will:

- Create a virtual environment in `.venv/`
    
- Install all dependencies defined in `pyproject.toml` / `poetry.lock`
    

---

## Environment Variables

Create the file `~/service/.env`:

```env
DATABASE_URL=xxxxxx
```

This project uses PostgreSQL

---

## Alembic (Database Migrations)

```bash
# 1. Generate a new migration file based on model changes
alembic revision --autogenerate -m "message"

# 2. Apply all pending migrations to the latest version (head)
alembic upgrade head

# 3. Roll back migrations to the previous version or a specific revision
alembic downgrade head
```

---
## Run the Application

Working directory: `~/service`

The legacy prototype entrypoint (`app.main`) was removed in refactor 0027.
The current runnable target is the **plaintext dev shell** (dev-only, no auth/encryption):

```bash
poetry env activate
uvicorn app.shell.lifespan:app
# → ws://127.0.0.1:8000/dev/ws?nick=alice  (preset dev users: alice/bob/carol/dave/eve/frank)
```

If this does not work, **open a new terminal** and try again.
