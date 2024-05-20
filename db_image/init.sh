#!/bin/bash

# создание нового пользователя для БД
echo "host all ${DB_USER} bot password" >> "$PGDATA/pg_hba.conf"
# разрешение хосту на репликацию
echo "host replication ${DB_REPL_USER} 0.0.0.0/0 md5" >> "$PGDATA/pg_hba.conf"

set -e
# Подключаемся к базе данных как суперпользователь и выполняем SQL команды
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    \c ${DB_NAME}

    -- Для репликации
    CREATE USER ${DB_REPL_USER} WITH REPLICATION ENCRYPTED PASSWORD '${DB_REPL_PASSWORD}';
    SELECT pg_create_physical_replication_slot('replication_slot');

    -- Создание дополнительного пользователя
    CREATE USER ${DB_USER} WITH PASSWORD '${DB_PASSWORD}';

    -- Создание таблиц, если они еще не были созданы
    CREATE TABLE IF NOT EXISTS email (
        id SERIAL PRIMARY KEY,
        email VARCHAR(255) NOT NULL
    );

    CREATE TABLE IF NOT EXISTS phone (
        id SERIAL PRIMARY KEY,
        phone_number VARCHAR(20) NOT NULL
    );

    -- Предоставление прав дополнительному пользователю только на эти таблицы
    GRANT SELECT, INSERT ON TABLE email TO ${DB_USER};
    GRANT SELECT, INSERT ON TABLE phone TO ${DB_USER};
    GRANT USAGE, SELECT ON SEQUENCE email_id_seq TO ${DB_USER};
    GRANT USAGE, SELECT ON SEQUENCE phone_id_seq TO ${DB_USER};
EOSQL
