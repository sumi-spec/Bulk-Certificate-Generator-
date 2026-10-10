#!/bin/sh
set -e

# If the command is running the API server (uvicorn), execute database migrations first
if [ "$1" = "uvicorn" ]; then
    echo "Running database schema migrations via Alembic..."
    alembic upgrade head
    echo "Database migrations successfully applied."
fi

# Execute the specified process with signal propagation
exec "$@"
