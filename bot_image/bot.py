import re
import os
from uuid import uuid4
from dotenv import load_dotenv

import logging
from logging.handlers import RotatingFileHandler

import paramiko
from socket import gaierror

from telegram import Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, ConversationHandler, CallbackContext
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler

import psycopg2
from psycopg2 import OperationalError, InterfaceError, DatabaseError
from contextlib import contextmanager


class DatabaseErrorHandled(Exception):
    """Исключение, выбрасываемое при ошибке базы данных."""

    def __init__(self, message="DB error"):
        self.message = message
        super().__init__(self.message)


class ConnectionFailedException(Exception):
    """Исключение, выбрасываемое при неудачном подключении."""

    def __init__(self, message="Connection failed"):
        self.message = message
        super().__init__(self.message)


def catch_db_failures(func):
    """Декоратор для обработки ошибок взаимодействия с базой данных."""
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except (OperationalError, InterfaceError, DatabaseError) as e:
            logger.error(f"DB error: {e}")
            raise DatabaseErrorHandled()

    return wrapper


class RemoteMachine:
    def __init__(self, hostname, username, password):
        self.hostname = hostname
        self.username = username
        self.password = password
        self.connected = False
        self.session = paramiko.SSHClient()
        self.session.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    @contextmanager
    def __get_connection(self):
        try:
            logger.info('Trying to connect via ssh')
            self.session.connect(self.hostname, username=self.username, password=self.password)
            logger.info(f'Connected to {self.hostname}')
            yield self.session
        except (gaierror,
                TimeoutError,
                paramiko.ssh_exception.NoValidConnectionsError,
                paramiko.ssh_exception.SSHException) as e:
            logger.info(f'Connection failed: {e}')
            raise ConnectionFailedException(f"ssh connection failed: {e}")
        finally:
            if self.connected:
                self.session.close()

    def __execute(self, command: str) -> str:
        with self.__get_connection() as connection:
            stdin, stdout, stderr = connection.exec_command(command)
            decoded_error = stderr.read().decode()
            if ': command not found' in decoded_error:
                logger.warning(f"Remote system doesn't support command {command}: {decoded_error}")
                return "Remote system doesn't support command"
            else:
                result = stdout.read().decode()
                return result

    def get_repl_logs(self):
        raw_logs = self.__execute('grep -E "ready to accept|START|STOP" /var/log/postgresql/*.log')
        return raw_logs

    def get_release(self):
        # данные о релизе ОС
        res = self.__execute('lsb_release -a')
        return res

    def get_uname(self):
        res = self.__execute('uname -m && hostname && uname -r')
        if res:
            # форматирование
            res = res.split('\n')
            return f'Arch: {res[0]}\nHostname: {res[1]}\nKernel: {res[2]}'
        else:
            return

    def get_uptime(self):
        res = self.__execute('uptime -p')
        if res:
            # форматирование
            return res.split('up')[1]
        else:
            return

    def get_df(self):
        res = self.__execute('df -h')
        return res

    def get_free(self):
        res = self.__execute('free -h')
        return res

    def get_mpstat(self):
        res = self.__execute('mpstat')
        if res:
            try:
                # форматирование
                res = [i for i in res.split('\n') if i]
                res = res[-1]
                res_values = res.split()
                usr = res_values[3]
                sys = res_values[5]
                iowait = res_values[6]
                idle = res_values[12]
                output = (f"Amusing CPU life:\n"
                          f"  - user-level code:   {usr} %\n"
                          f"  - system-level code: {sys} %\n"
                          f"  - i/o waiting:       {iowait} %\n"
                          f"  - idle:              {idle} %")
                return output
            except IndexError:
                return res
        else:
            return

    def get_w(self):
        res = self.__execute('w')
        if res:
            return res
        else:
            return

    def get_auths(self):
        res = self.__execute('last -10')
        return res

    def get_critical(self):
        res = self.__execute('journalctl -p err -n 5')
        return res

    def get_ps(self):
        res = self.__execute('ps -e')
        return res

    def get_ss(self):
        res = self.__execute('ss -tuln')
        return res

    def get_services(self):
        res = self.__execute('systemctl list-units --type=service')
        return res

    def get_apt_list(self, choice=None):
        if choice:
            # данные о конкретном пакете
            command = f'apt list {choice}'
        else:
            # Данные о всех пакетах
            command = 'apt list --installed'
        res = self.__execute(command)
        if res:
            # форматирование
            res = [i for i in res.split("\n") if (i and not ('Listing' in i))]
            res = '\n'.join(res)
            return f'Packages info:\n{res}'
        else:
            return


class DataBase:
    def __init__(self, database, user, password, host, port):
        self.database = database
        self.user = user
        self.password = password
        self.host = host
        self.port = port

    @contextmanager
    def _get_connection(self):
        conn = None
        try:
            conn = psycopg2.connect(dbname=self.database,
                                    user=self.user,
                                    password=self.password,
                                    host=self.host,
                                    port=self.port)
            yield conn
        except (OperationalError, InterfaceError, DatabaseError) as e:
            logger.warning('Database transaction failed and rolled back.')
            if conn:
                conn.rollback()
            raise ConnectionFailedException(f"Ошибка подключения к Базе Данных: {e}")
        finally:
            if conn:
                conn.close()

    @catch_db_failures
    def write_emails(self, emails):
        query = "INSERT INTO email (email) VALUES (%s)"
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                logger.debug(f"Writing email addresses to the database: {emails}")
                for email in emails:
                    cursor.execute(query, (email,))
                conn.commit()

    @catch_db_failures
    def write_phone_numbers(self, phone_numbers):
        query = "INSERT INTO phone (phone_number) VALUES (%s)"
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                logger.debug(f"Writing phone numbers to the database: {phone_numbers}")
                for number in phone_numbers:
                    cursor.execute(query, (number,))
                conn.commit()

    @catch_db_failures
    def read_emails(self):
        query = "SELECT email FROM email"
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query)
                return [row[0] for row in cursor.fetchall()]

    @catch_db_failures
    def read_phone_numbers(self):
        query = "SELECT phone_number FROM phone"
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query)
                return [row[0] for row in cursor.fetchall()]


# Конфигурация логгера
logger = logging.getLogger(__name__)
logger.info("Logger level set to INFO")
logger.setLevel(logging.INFO)  # Установка уровня логирования
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler = RotatingFileHandler('bot.log', maxBytes=5 * 1024 * 1024, backupCount=5)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)
logger.addHandler(stream_handler)

# получение чувствительных данных из .env
dotenv_filename = '.env'
if os.path.exists(f'./{dotenv_filename}'):
    load_dotenv()
logger.debug('Loading .env file')
logger.debug('Getting data from .env')
token = os.getenv('TOKEN')  # Telegram Bot API
ssh_hostname = os.getenv('RM_HOST')
ssh_username = os.getenv('RM_USER')
ssh_password = os.getenv('RM_PASSWORD')
db_host = os.getenv('DB_HOST')
db_port = os.getenv('DB_PORT')
db_user = os.getenv('DB_USER')
db_password = os.getenv('DB_PASSWORD')
db_database = os.getenv('DB_DATABASE')

# создание экземпляра RemoteMachineInfoGrabber
remote_machine = RemoteMachine(ssh_hostname, ssh_username, ssh_password)

# создание экземпляра DataBase
db = DataBase(database=db_database,
              user=db_user,
              password=db_password,
              host=db_host,
              port=db_port)

# хранение состояний диалогов
PHONE, EMAIL, CHECK_PASSWORD, GET_APT_LIST, SAVING_PHONE_DATA, SAVING_EMAIL_DATA = range(6)


def send_message_or_document(update: Update, context: CallbackContext, text):
    chat_id = update.effective_chat.id
    max_message_length = 4096

    # Проверяем, не пустая ли строка перед отправкой
    if text and len(text.strip()) > 0:
        if len(text) <= max_message_length:
            context.bot.send_message(chat_id=chat_id, text=text)
        else:
            filename = f"{uuid4().hex}.txt"
            try:
                with open(filename, 'w', encoding='utf-8') as file:
                    file.write(text)
                context.bot.send_document(chat_id=chat_id, document=open(filename, 'rb'))
            finally:
                if os.path.exists(filename):
                    os.remove(filename)
    else:
        logger.error("message can't be empty")
        # Можно также отправить сообщение об ошибке пользователю
        context.bot.send_message(chat_id=chat_id, text="Error occurred: got empty text from internal service")


def ask_to_database(update: Update, context: CallbackContext, info_type: str) -> int:
    # Создаем кнопки
    keyboard = [
        [InlineKeyboardButton("Yes", callback_data='Yes')],
        [InlineKeyboardButton("No", callback_data='No')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    # спрашиваем пользователя с помощью кнопок
    update.message.reply_text("Would you like to save this data in DB?", reply_markup=reply_markup)
    # возвращаем новое состояние в зависимости от типа информации
    return SAVING_PHONE_DATA if info_type == 'phone_numbers' else SAVING_EMAIL_DATA


def save_email_data_decision(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    if query.data == 'Yes':
        db.write_emails(context.user_data['email_addresses'])
        query.edit_message_text("Emails have been successfully saved")
    else:
        query.edit_message_text("Emails are not saved")
    return ConversationHandler.END


def save_phone_data_decision(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    if query.data == 'Yes':
        db.write_phone_numbers(context.user_data['phone_numbers'])
        query.edit_message_text("Phone numbers have been successfully saved")
    else:
        query.edit_message_text("Phone numbers are not saved")
    return ConversationHandler.END


def start(update: Update, _context: CallbackContext) -> None:
    # Приветствие, вывод возможностей
    user = update.effective_user
    logger.info(f'Showing greetings for: {user.name} {user.last_name} ({user.id})')
    possibilities = ('I can: '
                     '\n  -search emails and phone numbers in text '
                     '\n  -store them in a database'
                     '\n  -check password strength'
                     '\n  -display remote system data'
                     '\n  -save data to ')
    if user.first_name:
        response_message = f'Hi, {user.first_name}!\n\n{possibilities}'
    else:
        response_message = f'Hi!\n\n{possibilities}'
    update.message.reply_text(response_message)


def cancel(update: Update, _context: CallbackContext) -> int:
    # отмена операции, если пользователь выбрал команду с состоянием
    logger.info('Operation canceled')
    update.message.reply_text("Operation canceled")
    return ConversationHandler.END


def phone_reply(update: Update, _context: CallbackContext) -> int:
    # сообщение после получение команды о поиске номеров телефонов
    logger.info(f'find_phones dialog started: user {update.message.from_user.id} ')
    update.message.reply_text("Type in the text, and I'll find the phone numbers:")
    return PHONE


# Обновленная функция phone_cmd с универсальной функцией-предложением
def phone_cmd(update: Update, context: CallbackContext) -> int:
    phone_numbers = search_phone_numbers(update.message.text)
    if phone_numbers:
        response = '\n'.join(phone_numbers)
        context.user_data['phone_numbers'] = phone_numbers
        send_message_or_document(update, context, response)
        return ask_to_database(update, context, 'phone_numbers')
    else:
        update.message.reply_text("Can't find phone numbers here")
        return ConversationHandler.END


def search_phone_numbers(text):
    # Поиск номеров телефонов. Поддерживает разные форматы записи
    pattern = r"""
            (?:\+7|8|7)       # Начинается с "+7", "8", или "7"
            [\s\-\(\)]*       # Любое количество пробелов, дефисов, скобок
            (\d{3})           # Три цифры кода города или оператора
            [\s\-\(\)]*       # Любое количество пробелов, дефисов, скобок
            (\d{3})           # Первые три цифры номера
            [\s\-\(\)]*       # Любое количество пробелов, дефисов, скобок
            (\d{2})           # Следующие две цифры номера
            [\s\-\(\)]*       # Любое количество пробелов, дефисов, скобок
            (\d{2})           # Последние две цифры номера
            |                 # ИЛИ
            (\d{10})          # Просто последовательность из 10 цифр
        """
    clear_numbers = []
    matches = re.findall(pattern, text, re.VERBOSE)
    logger.debug(f'Found {len(matches)} matches')
    for match in matches:
        simple_sequence = match[-1]
        if simple_sequence:  # если формат без разделителей
            clear_numbers.append(simple_sequence)
        else:  # в других случаях
            formatted_number = f"{match[0]}{match[1]}{match[2]}{match[3]}"
            logger.debug(f'Appending {formatted_number} to recognized numbers')
            clear_numbers.append(formatted_number)
    return clear_numbers or None


def email_reply(update: Update, _context: CallbackContext) -> int:
    # сообщение после получение команды о поиске почт
    logger.info(f'find_emails command: user {update.message.from_user.id} ')
    update.message.reply_text("Type in the text, and I'll find the emails:")
    return EMAIL


def email_cmd(update: Update, context: CallbackContext) -> int:
    email_addresses = search_emails(update.message.text)
    if email_addresses:
        response = '\n'.join(email_addresses)
        context.user_data['email_addresses'] = email_addresses
        send_message_or_document(update, context, response)
        return ask_to_database(update, context, 'email_addresses')
    else:
        update.message.reply_text("Can't find emails here")
        return ConversationHandler.END


def search_emails(text):
    email_pattern = r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"
    found_emails = re.findall(email_pattern, text)
    logger.debug(f'Found {len(found_emails)} emails')
    return found_emails or None


def check_password_reply(update: Update, context: CallbackContext) -> int:
    # сообщение после получение команды о проверке пароля
    logger.info(f'check_password command: user {update.message.from_user.id} ')
    update.message.reply_text("Type in password:")
    return CHECK_PASSWORD


def check_password(text):
    # проверка пароля на сложность
    # условия, делающие пароль "простым"
    conditions_simple = [len(text) < 8,
                         not re.search(r"[A-Z]", text),
                         not re.search(r"[a-z]", text),
                         not re.search(r"[0-9]", text),
                         not re.search(r"[!@#$%^&*()]", text)]
    if any(conditions_simple):
        return 'Bad password'
    else:
        return 'Good password'


def check_password_cmd(update: Update, context: CallbackContext) -> int:
    # обработчик для команды проверки пароля
    simplicity = check_password(update.message.text)
    update.message.reply_text(simplicity)
    return ConversationHandler.END


def get_release_cmd(update: Update, context: CallbackContext) -> None:
    logger.info(f'get_release command: user {update.message.from_user.id} ')
    response = remote_machine.get_release()
    send_message_or_document(update, context, response)


def get_uname_cmd(update: Update, context: CallbackContext) -> None:
    logger.info(f'get_uname command: user {update.message.from_user.id} ')
    response = remote_machine.get_uname()
    send_message_or_document(update, context, response)


def get_uptime_cmd(update: Update, context: CallbackContext) -> None:
    logger.info(f'get_uptime command: user {update.message.from_user.id} ')
    response = remote_machine.get_uptime()
    send_message_or_document(update, context, response)


def get_df_cmd(update: Update, context: CallbackContext) -> None:
    logger.info(f'get_df command: user {update.message.from_user.id} ')
    response = remote_machine.get_df()
    send_message_or_document(update, context, response)


def get_free_cmd(update: Update, context: CallbackContext) -> None:
    logger.info(f'get_free command: user {update.message.from_user.id} ')
    response = remote_machine.get_free()
    send_message_or_document(update, context, response)


def get_mpstat_cmd(update: Update, context: CallbackContext) -> None:
    logger.info(f'get_mpstat command: user {update.message.from_user.id} ')
    response = remote_machine.get_mpstat()
    send_message_or_document(update, context, response)


def get_w_cmd(update: Update, context: CallbackContext) -> None:
    logger.info(f'get_w command: user {update.message.from_user.id} ')
    response = remote_machine.get_w()
    send_message_or_document(update, context, response)


def get_auths_cmd(update: Update, context: CallbackContext) -> None:
    logger.info(f'get_auths command: user {update.message.from_user.id} ')
    response = remote_machine.get_auths()
    send_message_or_document(update, context, response)


def get_critical_cmd(update: Update, context: CallbackContext) -> None:
    logger.info(f'get_critical command: user {update.message.from_user.id} ')
    response = remote_machine.get_critical()
    send_message_or_document(update, context, response)


def get_ps_cmd(update: Update, context: CallbackContext) -> None:
    logger.info(f'get_ps command: user {update.message.from_user.id} ')
    response = remote_machine.get_ps()
    send_message_or_document(update, context, response)


def get_ss_cmd(update: Update, context: CallbackContext) -> None:
    logger.info(f'get_ss command: user {update.message.from_user.id} ')
    response = remote_machine.get_ss()
    send_message_or_document(update, context, response)


def get_services_cmd(update: Update, context: CallbackContext) -> None:
    logger.info(f'get_services command: user {update.message.from_user.id} ')
    response = remote_machine.get_services()
    send_message_or_document(update, context, response)


def get_apt_list_reply(update: Update, context: CallbackContext) -> int:
    logger.info(f'get_apt_list command: user {update.message.from_user.id} ')
    # сообщение после получение команды запроса данных об установленных пакетах
    update.message.reply_text("Package name or all (!all):")
    return GET_APT_LIST


def get_apt_list_cmd(update: Update, context: CallbackContext) -> int:
    # обработчик для команды запроса данных об установленных пакетах
    choice = update.message.text
    if "!all" in choice:
        choice = None

    response = remote_machine.get_apt_list(choice)
    send_message_or_document(update, context, response)
    return ConversationHandler.END


def get_repl_logs_cmd(update: Update, context: CallbackContext) -> None:
    logger.info(f'get_repl_logs command: user {update.message.from_user.id} ')
    response = remote_machine.get_repl_logs()
    send_message_or_document(update, context, response)


@catch_db_failures
def get_emails_cmd(update: Update, context: CallbackContext) -> None:
    # Вызов метода DataBase для чтения email-ов
    logger.info(f'get_emails command: user {update.message.from_user.id} ')
    email_addresses = db.read_emails()
    response = "There are no emails" if not email_addresses else '\n'.join(email_addresses)
    send_message_or_document(update, context, response)


@catch_db_failures
def get_phone_numbers_cmd(update: Update, context: CallbackContext) -> None:
    logger.info(f'get_phone_numbers command: user {update.message.from_user.id} ')
    # Вызов метода DataBase для чтения телефонных номеров
    phone_numbers = db.read_phone_numbers()
    response = "There are no phone numbers" if not phone_numbers else '\n'.join(phone_numbers)
    send_message_or_document(update, context, response)


def main():
    # Создаем объект Updater для получения обновлений от Telegram
    logger.debug(
        f"Token: {token}, SSH: {ssh_hostname}@{ssh_username}, DB: {db_user}@{db_host}:{db_port}")
    updater = Updater(token, use_context=True)

    # Получаем Dispatcher, который используется для регистрации обработчиков
    dispatcher = updater.dispatcher

    # Создаем обработчик команды 'start', который будет реагировать на команду /start в чате.
    start_handler = CommandHandler('start', start)
    dispatcher.add_handler(start_handler)  # Добавляем обработчик команды 'start' в Dispatcher.

    # Инициализируем ConversationHandler для организации диалогов с пользователем (например, для поиска телефонов).
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('find_phone_number', phone_reply),
                      CommandHandler('find_email', email_reply),
                      CommandHandler('check_password', check_password_reply),
                      CommandHandler('get_apt_list', get_apt_list_reply)],

        states={PHONE: [MessageHandler(Filters.text & ~Filters.command, phone_cmd)],
                EMAIL: [MessageHandler(Filters.text & ~Filters.command, email_cmd)],
                CHECK_PASSWORD: [MessageHandler(Filters.text & ~Filters.command, check_password_cmd)],
                GET_APT_LIST: [MessageHandler(Filters.text & ~Filters.command, get_apt_list_cmd)],
                SAVING_PHONE_DATA: [CallbackQueryHandler(save_phone_data_decision)],
                SAVING_EMAIL_DATA: [CallbackQueryHandler(save_email_data_decision)], },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    # Регистрируем ConversationHandler в Dispatcher.

    dispatcher.add_handler(conv_handler)

    get_commands = {'get_phone_numbers': get_phone_numbers_cmd,
                    'get_emails': get_emails_cmd,
                    'get_release': get_release_cmd,
                    'get_uname': get_uname_cmd,
                    'get_uptime': get_uptime_cmd,
                    'get_df': get_df_cmd,
                    'get_free': get_free_cmd,
                    'get_mpstat': get_mpstat_cmd,
                    'get_w': get_w_cmd,
                    'get_auths': get_auths_cmd,
                    'get_critical': get_critical_cmd,
                    'get_ps': get_ps_cmd,
                    'get_ss': get_ss_cmd,
                    'get_services': get_services_cmd,
                    'get_repl_logs': get_repl_logs_cmd}

    # Добавляем обработчики команд из словаря в Dispatcher
    for cmd, func in get_commands.items():
        dispatcher.add_handler(CommandHandler(cmd, func))

    # бот будет регулярно, в активном режиме, опрашивать сервера Telegram на предмет новых сообщений
    logger.info("Telegram bot started polling for updates.")
    updater.start_polling()
    # Запускаем бота, так, чтобы он работал до принудительной остановки.
    updater.idle()
    logger.info("Telegram bot stopping gracefully.")


if __name__ == '__main__':
    main()

