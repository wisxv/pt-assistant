#!/bin/bash
set -e

# Проверка наличия openssh-server и его установка, если отсутствует
if ! dpkg -l | grep -qw openssh-server; then
    apt-get update && apt-get install -y openssh-server
    mkdir /var/run/sshd
fi

# Создание пользователя для SSH, если он еще не создан
if ! id "${RM_USER}" &>/dev/null; then
    useradd -m -s /bin/bash "${RM_USER}" && \
    echo "${RM_USER}:${RM_PASSWORD}" | chpasswd
fi

# Включение аутентификации по паролю для SSH, если еще не включено
if ! grep -q "^PasswordAuthentication yes" /etc/ssh/sshd_config; then
    echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config
fi

# Отключение доступа для root пользователя через SSH, если еще не отключено
if ! grep -q "^PermitRootLogin no" /etc/ssh/sshd_config; then
    sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin no/' /etc/ssh/sshd_config
    # Если строка "PermitRootLogin yes" существует, заменяем ее на "PermitRootLogin no"
    sed -i 's/PermitRootLogin yes/PermitRootLogin no/' /etc/ssh/sshd_config
fi

# Вызов оригинального entrypoint скрипта PostgreSQL
exec /usr/local/bin/docker-entrypoint.sh "$@"
