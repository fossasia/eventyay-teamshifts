#!/bin/sh
REPO_DIR=$(git rev-parse --show-toplevel)
GIT_DIR=$REPO_DIR/.git

if [ -z "$VIRTUAL_ENV" ]; then
    echo "VIRTUAL_ENV is not set; activate your virtual environment before running this script"
    exit 1
fi

VENV_ACTIVATE="$VIRTUAL_ENV/bin/activate"
if [ ! -f "$VENV_ACTIVATE" ]; then
    echo "Could not find activation script at $VENV_ACTIVATE"
    exit 1
fi

echo "#!/bin/sh" > "$GIT_DIR/hooks/pre-commit"
echo "set -e" >> "$GIT_DIR/hooks/pre-commit"
echo ". $VENV_ACTIVATE" >> "$GIT_DIR/hooks/pre-commit"
echo "ruff check --select I ." >> "$GIT_DIR/hooks/pre-commit"
echo "ruff format --check ." >> "$GIT_DIR/hooks/pre-commit"
echo "flake8 ." >> "$GIT_DIR/hooks/pre-commit"
chmod +x "$GIT_DIR/hooks/pre-commit"
