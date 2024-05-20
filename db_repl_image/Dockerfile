FROM postgres:latest

# Копируем скрипт запуска в контейнер и даем права на выполнение
COPY ./start-replica.sh /start-replica.sh
RUN chmod +x /start-replica.sh

# Устанавливаем скрипт запуска в качестве точки входа
ENTRYPOINT ["/start-replica.sh"]
