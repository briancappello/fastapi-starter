#!/bin/bash
set -e

# Load environment variables from .env file
if [[ -f .env ]]; then
    export $(grep -E '^SQL_DB_' .env | xargs)
else
    echo "Error: .env file not found"
    exit 1
fi

# Validate required variables
if [[ -z "$SQL_DB_HOST" || -z "$SQL_DB_PORT" || -z "$SQL_DB_USER" || -z "$SQL_DB_PASSWORD" || -z "$SQL_DB_NAME" ]]; then
    echo "Error: Missing required database configuration in .env"
    echo "Required: SQL_DB_HOST, SQL_DB_PORT, SQL_DB_USER, SQL_DB_PASSWORD, SQL_DB_NAME"
    exit 1
fi

export PGPASSWORD="$SQL_DB_PASSWORD"

MAIN_DB="$SQL_DB_NAME"
TEST_DB="${SQL_DB_NAME}_test"

echo "Resetting databases: '$MAIN_DB' and '$TEST_DB' on $SQL_DB_HOST"

for DB in "$MAIN_DB" "$TEST_DB"; do
    psql -h "$SQL_DB_HOST" -p "$SQL_DB_PORT" -U "$SQL_DB_USER" -d postgres -c "DROP DATABASE IF EXISTS \"$DB\";"
    psql -h "$SQL_DB_HOST" -p "$SQL_DB_PORT" -U "$SQL_DB_USER" -d postgres -c "CREATE DATABASE \"$DB\";"
done

echo "Done. Databases reset successfully."
