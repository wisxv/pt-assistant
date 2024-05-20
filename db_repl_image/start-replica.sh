#!/bin/bash

echo 'Cleaning up data directory...'
rm -rf /var/lib/postgresql/data/*

# Ожидаем доступности основной базы данных и выполняем базовый бэкап
until pg_basebackup --pgdata=/var/lib/postgresql/data -R --slot=replication_slot --host=${DB_HOST} --port=5432; do
  echo 'Waiting for primary to connect...'
  sleep 1s
done

echo 'Backup done, starting replica...'
chmod 0700 /var/lib/postgresql/data

# Запускаем основной процесс PostgreSQL
exec docker-entrypoint.sh postgres
