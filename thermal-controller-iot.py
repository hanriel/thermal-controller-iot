#!/usr/bin/env python3
"""
Система мониторинга температуры и влажности с датчиком BME280
"""

from dataclasses import dataclass
import time
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
import yaml
import sqlite3
from flask import Flask, render_template_string, jsonify

# CircuitPython для BME280
try:
    import board
    from adafruit_bme280 import basic as adafruit_bme280
    BME280_AVAILABLE = True
except NotImplementedError:
    BME280_AVAILABLE = False
    print("Внимание: CircuitPython библиотеки не установлены. Используется режим эмуляции.")

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('sensor_monitor.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

@dataclass
class SensorReading:
    """Класс для хранения данных с датчика"""
    timestamp: datetime
    temperature: float
    humidity: float
    pressure: float
    altitude: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Конвертация в словарь для JSON"""
        return {
            'timestamp': self.timestamp.isoformat(),
            'temperature': round(self.temperature, 2),
            'humidity': round(self.humidity, 2),
            'pressure': round(self.pressure, 2),
            'altitude': round(self.altitude, 2) if self.altitude else None
        }

class ThermalSensor:
    """Класс для работы с датчиком BME280 через CircuitPython"""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Инициализация датчика BME280
        
        Args:
            config: Конфигурация из config.yaml
        """
        self.config = config['sensor']
        self.i2c_bus = self.config['i2c_bus']
        self.i2c_address = self.config['i2c_address']
        self.sensor = None
        
        self._init_sensor()
    
    def _init_sensor(self):
        """Инициализация датчика через I2C"""
        try:
            if not BME280_AVAILABLE:
                raise ImportError("CircuitPython библиотеки не установлены")
            
            # Создаем I2C соединение
            i2c = board.I2C()
            
            # Инициализируем BME280
            self.sensor = adafruit_bme280.Adafruit_BME280_I2C(
                i2c, 
                address=self.i2c_address
            )
            
            # Настройка параметров
            self.sensor.sea_level_pressure = self.config.get('sea_level_pressure', 1013.25)
            
            # Калибровочные смещения
            temp_offset = self.config.get('temperature_offset', 0.0)
            hum_offset = self.config.get('humidity_offset', 0.0)
            pres_offset = self.config.get('pressure_offset', 0.0)
            
            # В CircuitPython библиотеке нет прямого offset, 
            # но мы можем компенсировать при чтении
            self.temp_offset = temp_offset
            self.hum_offset = hum_offset
            self.pres_offset = pres_offset
            
            logger.info(f"BME280 инициализирован на I2C-шине {self.i2c_bus}, адрес {hex(self.i2c_address)}")
            
        except Exception as e:
            logger.error(f"Ошибка инициализации BME280: {e}")
            self.sensor = None
    
    def read(self) -> Optional[SensorReading]:
        """
        Чтение данных с датчика
        
        Returns:
            SensorReading или None в случае ошибки
        """
        if self.sensor is None:
            # Режим эмуляции для тестирования
            return self._read_mock()
        
        try:
            # Чтение данных с компенсацией смещений
            temperature = self.sensor.temperature + self.temp_offset
            humidity = self.sensor.humidity + self.hum_offset
            pressure = self.sensor.pressure + self.pres_offset
            altitude = self.sensor.altitude
            
            return SensorReading(
                timestamp=datetime.now(),
                temperature=temperature,
                humidity=humidity,
                pressure=pressure,
                altitude=altitude
            )
            
        except Exception as e:
            logger.error(f"Ошибка чтения BME280: {e}")
            return None
    
    def _read_mock(self) -> SensorReading:
        """Эмуляция данных датчика для тестирования"""
        # Имитация суточных колебаний
        hour = datetime.now().hour
        base_temp = 22.0
        
        if 2 <= hour <= 6:  # Ночь
            temp_variation = -3.0
        elif 12 <= hour <= 16:  # День
            temp_variation = 3.0
        else:
            temp_variation = 0.0
        
        import random
        temperature = base_temp + temp_variation + random.uniform(-0.5, 0.5)
        humidity = 45.0 + random.uniform(-5, 5)
        pressure = 1013.25 + random.uniform(-10, 10)
        
        return SensorReading(
            timestamp=datetime.now(),
            temperature=round(temperature, 2),
            humidity=round(humidity, 2),
            pressure=round(pressure, 2),
            altitude=100.0 + random.uniform(-10, 10)
        )
    
    def is_connected(self) -> bool:
        """Проверка подключения датчика"""
        if self.sensor is None:
            return False
        
        try:
            # Попытка чтения для проверки
            _ = self.sensor.temperature
            return True
        except:
            return False

class SimpleDatabase:
    """Простая база данных SQLite"""
    def __init__(self, db_path='sensor_data.db'):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        """Инициализация таблицы"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS measurements
                     (id INTEGER PRIMARY KEY,
                      timestamp DATETIME,
                      temperature REAL,
                      humidity REAL,
                      pressure REAL)''')
        conn.commit()
        conn.close()
    
    def save_reading(self, temp, humidity, pressure):
        """Сохранить показание"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("INSERT INTO measurements (timestamp, temperature, humidity, pressure) VALUES (?, ?, ?, ?)",
                  (datetime.now().isoformat(), temp, humidity, pressure))
        conn.commit()
        conn.close()
    
    def get_recent(self, limit=100):
        """Получить последние записи"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM measurements ORDER BY timestamp DESC LIMIT ?", (limit,))
        rows = c.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    def get_last_hour(self):
        """Получить данные за последний час"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        hour_ago = datetime.now().timestamp() - 3600
        c.execute("SELECT * FROM measurements WHERE timestamp > ? ORDER BY timestamp ASC",
                  (datetime.fromtimestamp(hour_ago).isoformat(),))
        rows = c.fetchall()
        conn.close()
        return [dict(row) for row in rows]

class SensorMonitor:
    """Основной класс системы мониторинга"""
    
    def __init__(self, config_path: str = "config.yaml"):
        self.config = self.load_config(config_path)
        self.running = False
        
        # Инициализация компонентов
        self.sensor = ThermalSensor(self.config)
        self.db = SimpleDatabase()
        self.web_app = self.create_web_app()
        
        logger.info("Система мониторинга инициализирована")
    
    def load_config(self, config_path: str) -> Dict[str, Any]:
        """Загрузка конфигурации из YAML файла"""
        try:
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
            
            return config
        except Exception as e:
            logger.error(f"Ошибка загрузки конфигурации: {e}")
            # Конфигурация по умолчанию
            return self._default_config()
    
    def _default_config(self) -> Dict[str, Any]:
        """Конфигурация по умолчанию"""
        return {
            'device': {
                'name': 'raspberry-pi-climate',
                'location': 'кабинет'
            },
            'sensor': {  # Изменено с bme280 на sensor
                'i2c_bus': 1,
                'i2c_address': 0x76,
                'sea_level_pressure': 1013.25,
                'read_interval': 10,
                'temperature_offset': 0.0,
                'humidity_offset': 0.0,
                'pressure_offset': 0.0
            },
            'logging': {
                'level': 'INFO',
                'file': './sensor_monitor.log',
                'max_size_mb': 10,
                'backup_count': 5
            }
        }
    
    def create_web_app(self):
        """Создание Flask приложения"""
        app = Flask(__name__)
        
        @app.route('/')
        def index():
            """Главная страница"""
            return render_template_string(
                'index.html',
                device_name=self.config['device']['name'],
                location=self.config['device']['location'],
                status_text='Датчик подключен' if self.sensor.is_connected() else 'Датчик отключен',
                status_class='online' if self.sensor.is_connected() else 'offline'
            )
        
        @app.route('/api/current')
        def api_current():
            """Текущие данные"""
            reading = self.sensor.read()
            if reading:
                self.db.save_reading(reading.temperature, reading.humidity, reading.pressure)
            
            return jsonify({
                'success': True,
                'sensor_connected': self.sensor.is_connected(),
                'stats': reading
            })
        
        @app.route('/api/history')
        def api_history():
            """История данных"""
            from flask import request
            hours = int(request.args.get('hours', 1)) if 'request' in locals() else 1
            
            
            if hours == 1:
                data = self.db.get_last_hour()
            else:
                limit = hours * 60 // self.config['sensor']['read_interval']
                data = self.db.get_recent(limit)
            
            return jsonify({
                'success': True,
                'data': data
            })
        
        @app.route('/api/health')
        def api_health():
            """Статус системы"""
            return jsonify({
                'status': 'ok',
                'sensor_connected': self.sensor.is_connected(),
                'timestamp': datetime.now().isoformat()
            })
        
        return app

    def _data_collection_thread(self):
        """Поток сбора данных с датчика"""
        logger.info("Запуск потока сбора данных")
        
        read_interval = self.config['sensor']['read_interval']
        
        while self.running:
            try:
                # Чтение данных с датчика
                reading = self.sensor.read()
                
                if reading:
                    # Логирование
                    logger.debug(f"Данные: {reading.temperature}°C, {reading.humidity}%")
                
                # Ожидание до следующего чтения
                time.sleep(read_interval)
                
            except Exception as e:
                logger.error(f"Ошибка в потоке сбора данных: {e}")
                time.sleep(read_interval)
    
    def start(self):
        """Запуск системы мониторинга"""
        self.running = True
        
        # Запуск потока сбора данных
        data_thread = threading.Thread(target=self._data_collection_thread, daemon=True)
        data_thread.start()
    
    def stop(self):
        """Остановка системы"""
        self.running = False
        logger.info("Система мониторинга остановлена")

def main():
    """Точка входа в программу"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Система мониторинга температуры и влажности')
    parser.add_argument('--config', default='config.yaml', help='Путь к файлу конфигурации')
    parser.add_argument('--test-sensor', action='store_true', help='Тестирование датчика')
    
    args = parser.parse_args()
    
    if args.test_sensor:
        # Тестовый режим
        config = {}
        if Path(args.config).exists():
            with open(args.config, 'r') as f:
                config = yaml.safe_load(f)
        
        sensor = ThermalSensor(config)
        print("Тестирование датчика...")
        
        for i in range(5):
            reading = sensor.read()
            if reading:
                print(f"Показание {i+1}: {reading.temperature}°C, {reading.humidity}%, {reading.pressure} hPa")
            else:
                print("Ошибка чтения датчика")
            time.sleep(2)
        
        print(f"Датчик подключен: {sensor.is_connected()}")
        return
    
    # Основной режим работы
    monitor = SensorMonitor(args.config)
    monitor.start()

if __name__ == "__main__":
    main()