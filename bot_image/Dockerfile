# Используем официальный образ Python 3.9
FROM python:3.9

# Создаём рабочую директорию внутри контейнера
WORKDIR /telegram_bot

# Копируем файлы проекта в рабочую директорию
COPY ./bot.py .
COPY ./requirements.txt .

# Устанавливаем зависимости 
RUN pip install --no-cache-dir -r requirements.txt

# Запускаем бота при старте контейнера
CMD ["python", "./bot.py"]
