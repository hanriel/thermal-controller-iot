#!/bin/bash
# Установка системы мониторинга температуры и влажности на Raspberry Pi

set -e  # Прерывать выполнение при ошибках

echo "========================================="
echo " Установка системы мониторинга климата"
echo "========================================="

# Проверка на запуск от root
if [ "$EUID" -ne 0 ]; then 
    echo "ОШИБКА: Запустите скрипт с правами root:"
    echo "  sudo ./install.sh"
    exit 1
fi

# Определение пользователя для сервиса
SERVICE_USER="pi"
if ! id "$SERVICE_USER" &>/dev/null; then
    echo "Пользователь $SERVICE_USER не найден, используется текущий пользователь"
    SERVICE_USER="$SUDO_USER"
fi

# Параметры установки
INSTALL_DIR="/opt/climate-monitor"
SERVICE_NAME="climate-monitor"
VENV_DIR="$INSTALL_DIR/venv"

echo "[1] Обновление системы..."
apt-get update
apt-get upgrade -y

echo "[2] Установка системных зависимостей..."
apt-get install -y \
    python3-pip \
    python3-venv \
    python3-dev \
    i2c-tools \
    libgpiod2 \
    git \
    curl \
    wget

# Включение I2C интерфейса
echo "[3] Настройка I2C интерфейса..."
if ! grep -q "i2c-dev" /etc/modules; then
    echo "i2c-dev" >> /etc/modules
    echo "  → i2c-dev добавлен в /etc/modules"
fi

if ! grep -q "dtparam=i2c_arm=on" /boot/config.txt; then
    echo "dtparam=i2c_arm=on" >> /boot/config.txt
    echo "  → I2C включен в /boot/config.txt"
fi

if ! grep -q "dtparam=i2c_baudrate=400000" /boot/config.txt; then
    echo "dtparam=i2c_baudrate=400000" >> /boot/config.txt
    echo "  → Установлена скорость I2C 400kHz"
fi

# Проверка наличия датчика
echo "[4] Проверка подключения датчиков..."
echo "Сканирование I2C шины 1:"
i2cdetect -y 1 || echo "Предупреждение: Не удалось сканировать I2C шину"

# Создание директории для установки
echo "[5] Создание структуры директорий..."
mkdir -p "$INSTALL_DIR"
mkdir -p "/var/log/temperature-monitor"
mkdir -p "/var/lib/$SERVICE_NAME"

# Копирование файлов проекта
echo "[6] Копирование файлов проекта..."
cp -r ./* "$INSTALL_DIR/" 2>/dev/null || true
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
chown -R "$SERVICE_USER:$SERVICE_USER" "/var/log/temperature-monitor"
chown -R "$SERVICE_USER:$SERVICE_USER" "/var/lib/$SERVICE_NAME"

# Создание виртуального окружения
echo "[7] Создание виртуального окружения..."
su - "$SERVICE_USER" -c "cd '$INSTALL_DIR' && python3 -m venv '$VENV_DIR'"

# Установка Python библиотек
echo "[8] Установка Python библиотек..."
su - "$SERVICE_USER" -c "
    cd '$INSTALL_DIR'
    source '$VENV_DIR/bin/activate'
    pip install --upgrade pip setuptools wheel
    pip install -r requirements.txt --no-cache-dir
"

# Упрощенные зависимости (если нужно для Raspberry Pi Zero)
echo "[9] Установка облегченных зависимостей (опционально)..."
if [ -f "$INSTALL_DIR/requirements-light.txt" ]; then
    su - "$SERVICE_USER" -c "
        cd '$INSTALL_DIR'
        source '$VENV_DIR/bin/activate'
        pip install -r requirements-light.txt --no-cache-dir
    "
fi

# Настройка прав
echo "[10] Настройка прав доступа..."
chmod +x "$INSTALL_DIR/thermal-controller-iot.py"
chmod 644 "$INSTALL_DIR/config.yaml"
chmod 644 "$INSTALL_DIR/requirements.txt"

# Создание systemd сервиса
echo "[11] Создание systemd сервиса..."
cat > "/etc/systemd/system/$SERVICE_NAME.service" << EOF
[Unit]
Description=Climate Monitor Service (BME280 Temperature/Humidity)
After=network.target multi-user.target
Wants=network.target
StartLimitIntervalSec=500
StartLimitBurst=5

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
Environment="PYTHONUNBUFFERED=1"
Environment="PATH=$VENV_DIR/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=$VENV_DIR/bin/python $INSTALL_DIR/thermal-controller-iot.py
Restart=on-failure
RestartSec=10
StandardOutput=syslog
StandardError=syslog
SyslogIdentifier=$SERVICE_NAME

# Защита службы
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
EOF

# Создание конфигурации logrotate
echo "[12] Настройка ротации логов..."
cat > "/etc/logrotate.d/$SERVICE_NAME" << EOF
/var/log/temperature-monitor/*.log {
    daily
    missingok
    rotate 7
    compress
    delaycompress
    notifempty
    create 644 $SERVICE_USER $SERVICE_USER
    sharedscripts
    postrotate
        systemctl kill -s HUP $SERVICE_NAME.service >/dev/null 2>&1 || true
    endscript
}
EOF

# Перезагрузка systemd и запуск сервиса
echo "[13] Настройка автозапуска..."
systemctl daemon-reload
systemctl enable "$SERVICE_NAME.service"

# Тестирование конфигурации
echo "[14] Тестирование конфигурации..."
if systemctl start "$SERVICE_NAME.service"; then
    sleep 2
    if systemctl is-active --quiet "$SERVICE_NAME.service"; then
        echo "  ✅ Сервис успешно запущен"
    else
        echo "  ⚠️  Сервис запущен, но возможно есть ошибки"
        echo "  Проверьте логи: journalctl -u $SERVICE_NAME -f"
    fi
else
    echo "  ❌ Ошибка запуска сервиса"
fi

# Создание скрипта управления
echo "[15] Создание утилиты управления..."
cat > "/usr/local/bin/climate-monitor" << EOF
#!/bin/bash
# Утилита управления климатическим монитором

case "\$1" in
    start)
        sudo systemctl start $SERVICE_NAME.service
        ;;
    stop)
        sudo systemctl stop $SERVICE_NAME.service
        ;;
    restart)
        sudo systemctl restart $SERVICE_NAME.service
        ;;
    status)
        sudo systemctl status $SERVICE_NAME.service
        ;;
    logs)
        sudo journalctl -u $SERVICE_NAME.service -f "\${@:2}"
        ;;
    test)
        cd $INSTALL_DIR
        $VENV_DIR/bin/python thermal-controller-iot.py --test-sensor
        ;;
    update)
        cd $INSTALL_DIR
        sudo -u $SERVICE_USER git pull
        sudo systemctl restart $SERVICE_NAME.service
        ;;
    *)
        echo "Использование: climate-monitor {start|stop|restart|status|logs|test|update}"
        echo "  start   - запустить сервис"
        echo "  stop    - остановить сервис"
        echo "  restart - перезапустить сервис"
        echo "  status  - статус сервиса"
        echo "  logs    - просмотр логов (добавьте -n 50 для последних 50 строк)"
        echo "  test    - протестировать датчик"
        echo "  update  - обновить систему из git"
        exit 1
        ;;
esac
EOF

chmod +x "/usr/local/bin/climate-monitor"

# Создание конфигурации для Raspberry Pi Zero (опционально)
if [ -f /proc/device-tree/model ] && grep -q "Zero" /proc/device-tree/model; then
    echo "[16] Настройка для Raspberry Pi Zero..."
    # Уменьшаем требования к памяти
    sed -i 's/read_interval: 30/read_interval: 60/' "$INSTALL_DIR/config.yaml"
    echo "  → Увеличен интервал чтения для экономии ресурсов"
fi

echo ""
echo "========================================="
echo " Установка завершена успешно!"
echo "========================================="
echo ""
echo "Информация о системе:"
echo "  • Директория установки: $INSTALL_DIR"
echo "  • Пользователь сервиса: $SERVICE_USER"
echo "  • Виртуальное окружение: $VENV_DIR"
echo ""
echo "Основные команды:"
echo "  climate-monitor start      # Запустить сервис"
echo "  climate-monitor stop       # Остановить сервис"
echo "  climate-monitor status     # Статус сервиса"
echo "  climate-monitor logs       # Просмотр логов в реальном времени"
echo "  climate-monitor test       # Тестирование датчика"
echo ""
echo "Проверка датчика:"
echo "  cd $INSTALL_DIR"
echo "  source $VENV_DIR/bin/activate"
echo "  python thermal-controller-iot.py --test-sensor"
echo ""
echo "Конфигурационные файлы:"
echo "  • Основной конфиг: $INSTALL_DIR/config.yaml"
echo "  • Логи: /var/log/temperature-monitor/"
echo ""
echo "Для перезагрузки системы и активации I2C:"
echo "  sudo reboot"
echo ""

# Предложение проверить датчик
read -p "Хотите протестировать датчик прямо сейчас? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Тестирование датчика BME280..."
    su - "$SERVICE_USER" -c "
        cd '$INSTALL_DIR'
        source '$VENV_DIR/bin/activate'
        python thermal-controller-iot.py --test-sensor
    "
fi

echo "Готово! Система мониторинга установлена."