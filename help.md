# srvmon Help

`srvmon` - локальная CLI/TUI/Web утилита мониторинга сервера. Она собирает метрики CPU, памяти, дисков, сети, процессов, хранит историю в SQLite, строит отчеты, экспортирует данные, фиксирует алерты и показывает локальный веб-дашборд.

## Быстрый Старт

```bash
python -m venv .venv
```

Windows:

```powershell
.venv\Scripts\activate
pip install -e .
srvmon live
```

Linux/macOS:

```bash
source .venv/bin/activate
pip install -e .
srvmon live
```

После запуска `srvmon live` открываются:

- консольный TUI-интерфейс;
- локальный веб-дашборд, обычно `http://127.0.0.1:8765`.

## Файлы И Данные

По умолчанию srvmon использует:

```text
~/.srvmon/config.toml
~/.srvmon/data/metrics.sqlite3
~/.srvmon/reports/
~/.srvmon/srvmon.pid
~/.srvmon/srvmon.log
~/.srvmon/srvmon-web.log
```

Если нет прав на `~/.srvmon/reports`, экспорт сохраняется в:

```text
./.srvmon/reports/
```

История хранится максимум 62 дня, если в конфиге не указано другое значение.

## Периоды

Периоды используются в `report`, `export`, `alerts`:

| Аргумент | Значение |
| --- | --- |
| `-1h` | последний час |
| `-1d` | последний день, значение по умолчанию |
| `-1w` | последняя неделя |
| `-1m` | последний месяц, 31 день |
| `-2m` | два месяца, 62 дня |

## Команды

### `srvmon live`

Живой мониторинг в консоли с автоматическим обновлением и локальным веб-дашбордом.

```bash
srvmon live
srvmon live -0.5s
srvmon live -3s
srvmon live -10s
```

Интервал:

- по умолчанию: из `config.toml`, обычно `2.0`;
- минимум: `0.5s`;
- максимум: `10s`;
- если значение меньше/больше диапазона, srvmon использует ближайшее допустимое.

Аргументы:

| Аргумент | Описание |
| --- | --- |
| `interval` | интервал обновления: `-0.5s`, `-3s`, `-10s` |
| `--storage-path PATH` | использовать другую SQLite БД |
| `--no-storage` | не сохранять метрики в БД |
| `--no-web` | не запускать веб-дашборд |
| `--web-port PORT` | предпочитаемый порт веб-дашборда, по умолчанию `8765` |

Примеры:

```bash
srvmon live --web-port 9000
srvmon live --no-web
srvmon live --storage-path ./metrics.sqlite3
```

### `srvmon start`

Запускает фоновый сбор метрик как отдельный процесс.

```bash
srvmon start
```

Использует интервал:

```toml
[intervals]
collection_seconds = 2.0
```

PID хранится в:

```text
~/.srvmon/srvmon.pid
```

Лог фонового процесса:

```text
~/.srvmon/srvmon.log
```

### `srvmon stop`

Останавливает фоновый сбор метрик.

```bash
srvmon stop
```

Если PID-файл устарел, команда удалит его и сообщит, что сервис не запущен.

### `srvmon status`

Быстрый текущий обзор состояния системы.

```bash
srvmon status
```

Показывает:

- Health Score;
- статус фонового сервиса;
- путь к конфигу;
- путь к БД;
- Load, CPU, RAM, Swap;
- Disk usage, Disk I/O, latency;
- Network traffic и errors/drops;
- top CPU/RAM процессов;
- таблицу цветовых порогов.

### `srvmon report`

Отчет и анализ за период.

```bash
srvmon report
srvmon report -1h
srvmon report -1d
srvmon report -1w
srvmon report -1m
srvmon report -2m
```

Аргументы:

| Аргумент | Описание |
| --- | --- |
| `period` | период: `-1h`, `-1d`, `-1w`, `-1m`, `-2m` |
| `--storage-path PATH` | читать отчет из другой SQLite БД |

Отчет показывает:

- Health Score;
- latest/avg/max/change по ключевым метрикам;
- Load Average 1/5/15;
- CPU;
- RAM и Swap;
- Disk Usage и Disk I/O;
- Network Traffic;
- Errors/Drops;
- top CPU/RAM процессов за период;
- anomaly detection;
- самый тяжелый процесс за период;
- пиковую нагрузку;
- рост дисков;
- время в WARN/CRIT;
- рекомендации.

### `srvmon export`

Экспортирует историю в файл.

```bash
srvmon export --format json -1d
srvmon export --format csv -1m
srvmon export --format html -1w
```

Если `--output` не указан, файл сохраняется автоматически:

```text
~/.srvmon/reports/srvmon-last-1d-YYYY-MM-DD_HH-MM-SS.json
```

Аргументы:

| Аргумент | Описание |
| --- | --- |
| `period` | период: `-1h`, `-1d`, `-1w`, `-1m`, `-2m` |
| `--format json` | экспорт в JSON |
| `--format csv` | экспорт в CSV |
| `--format html` | экспорт в HTML |
| `--limit N` | ограничить количество sample-записей |
| `--output PATH` | сохранить в конкретный файл |
| `--storage-path PATH` | читать данные из другой SQLite БД |

Примеры:

```bash
srvmon export --format json -1d
srvmon export --format csv -1m --limit 1000
srvmon export --format html -1w --output report.html
```

Форматы:

- JSON: `samples`, `disk_usage`, `top_processes`;
- CSV: плоская таблица `metric_samples`;
- HTML: самодостаточный файл с таблицами.


### Active Alerts

???????? ?????????????? ?????? ?? ??????????? ????????? ????????. ??? ???????????? ?? ??????? ??????? `srvmon live` ? ? ????????? ???-??????. ?????????????? ?????????????? ?????? ???????? ?? ???????? ?????? ?????? ? ?? ???????????? ? ????, Telegram ??? webhook.

### `srvmon cleanup`

Удаляет старые записи из SQLite.

```bash
srvmon cleanup
srvmon cleanup --storage-path ./metrics.sqlite3
```

Срок хранения задается в конфиге:

```toml
[storage]
retention_days = 62
```

### `srvmon doctor`

Диагностика окружения.

```bash
srvmon doctor
```

Проверяет:

- версию Python;
- доступность SQLite;
- права на `~/.srvmon`;
- доступ к процессам;
- поддержку temperature/fan sensors через `psutil`.

## Веб-Дашборд

`srvmon live` автоматически поднимает локальный веб-дашборд:

```text
http://127.0.0.1:8765
```

Если порт занят, выбирается ближайший свободный порт.

Возможности:

- графики CPU;
- графики RAM;
- графики Network MB/s;
- графики Disk I/O;
- состояние дисков;
- top-процессы;
- выбор периода;
- кнопки экспорта JSON/CSV/HTML.

Отключить веб:

```bash
srvmon live --no-web
```

Выбрать порт:

```bash
srvmon live --web-port 9000
```

Лог веб-процесса:

```text
~/.srvmon/srvmon-web.log
```

## Конфигурация

Конфиг создается автоматически:

```text
~/.srvmon/config.toml
```

Пример:

```toml
[intervals]
live_refresh_seconds = 2.0
collection_seconds = 2.0

[storage]
database_path = "~/.srvmon/data/metrics.sqlite3"
retention_days = 62

[thresholds.cpu]
warning = 70
critical = 90

[thresholds.ram]
warning = 75
critical = 90

[monitoring]
disks = []
interfaces = []
```

### Интервалы

```toml
[intervals]
live_refresh_seconds = 2.0
collection_seconds = 2.0
```

- `live_refresh_seconds` - интервал TUI, если не указан CLI interval;
- `collection_seconds` - интервал фонового сбора для `srvmon start`.

### Хранилище

```toml
[storage]
database_path = "~/.srvmon/data/metrics.sqlite3"
retention_days = 62
```

- `database_path` - путь к SQLite БД;
- `retention_days` - сколько дней хранить историю.

### Пороги

```toml
[thresholds.cpu]
warning = 70
critical = 90
```

Поддерживаемые ключи:

- `load_ratio`
- `cpu`
- `ram`
- `swap`
- `disk`
- `disk_latency`
- `network_errors`
- `process_cpu`
- `process_ram`

### Диски И Интерфейсы

Пустые списки означают “мониторить все доступное”.

```toml
[monitoring]
disks = []
interfaces = []
```

Linux:

```toml
[monitoring]
disks = ["/", "/var"]
interfaces = ["eth0"]
```

Windows:

```toml
[monitoring]
disks = ["C:\\"]
interfaces = ["Ethernet"]
```

## Цветовые Пороги По Умолчанию

| Метрика | Зеленый | Желтый | Красный |
| --- | --- | --- | --- |
| Load Average | < 70% от logical CPU | >= 70% | >= 100% |
| CPU utilization | < 70% | >= 70% | >= 90% |
| RAM used | < 75% | >= 75% | >= 90% |
| Swap used | < 20% | >= 20% | >= 50% |
| Disk usage | < 80% | >= 80% | >= 90% |
| Disk latency | < 20 ms | >= 20 ms | >= 50 ms |
| Network errors/drops | 0 новых | >= 1 | >= 10 |
| Process CPU | < 70% | >= 70% | >= 90% |
| Process RAM | < 10% | >= 10% | >= 25% |

Disk I/O throughput и Network throughput считаются информационными метриками: универсального критического порога без знания железа и канала нет.

## Health Score

`srvmon status` и `srvmon report` показывают:

```text
Health Score: 82/100 | Status: WARNING
```

Оценка строится по:

- Load;
- CPU;
- RAM;
- Swap;
- Disk;
- Network errors/drops.

## Anomaly Detection

`srvmon report` считает простую статистику:

- baseline по истории;
- z-score;
- процент отклонения от типичного поведения;
- сравнение с последними днями.

Пример сообщения:

```text
Network out is above typical behavior by 240% (z=3.1)
```

## Схема SQLite

Основные таблицы:

| Таблица | Назначение |
| --- | --- |
| `metric_samples` | основные метрики по времени |
| `disk_usage` | usage дисков по sample |
| `top_processes` | top CPU/RAM процессов |
| `srvmon_config` | активная конфигурация |

## Типичные Проблемы

### `127.0.0.1 refused to connect`

Причины:

- веб-процесс не стартовал;
- порт занят;
- старый пакет без fallback web-server;
- зависимость FastAPI/uvicorn отсутствует, но fallback тоже упал.

Что сделать:

```bash
srvmon live --web-port 9000
```

Проверить лог:

```text
~/.srvmon/srvmon-web.log
```

Переустановить проект:

```bash
pip install -e .
```

### `fastapi` или `uvicorn` не установлены

Сейчас srvmon умеет работать без них через встроенный `http.server`. Но для FastAPI-режима можно установить зависимости:

```bash
pip install -e .
```

Или явно:

```bash
pip install fastapi uvicorn
```

### Нет данных в отчете

Причины:

- сбор еще не запускался;
- используется другая БД;
- выбран период без данных.

Что сделать:

```bash
srvmon live
```

Подождать 10-20 секунд и выполнить:

```bash
srvmon report -1h
```

Проверить путь к БД:

```bash
srvmon status
```

### Экспорт сохраняется не в `~/.srvmon/reports`

Если нет прав на домашнюю папку, srvmon использует fallback:

```text
./.srvmon/reports/
```

Можно указать путь явно:

```bash
srvmon export --format html -1d --output ./report.html
```

### Нет sensors / temperature / fan speed

Это нормально на части Windows-систем и виртуальных машин. Проверьте:

```bash
srvmon doctor
```

На Linux sensors обычно требуют поддержку ядра и доступные hwmon/lm-sensors.


Причины:

- команда недоступна;
- нет прав;
- ОС не Linux;

Проверка:

```bash
srvmon doctor
```

### `System Idle Process` или CPU больше 100%

В текущей версии top-процессы нормализуются к общей CPU-мощности и idle-процессы исключаются. Если видите старые значения в отчете, это может быть историческая запись из старой версии БД.

### `srvmon stop` говорит, что сервис не запущен

Возможные причины:

- `srvmon start` не запускался;
- процесс был завершен вручную;
- PID-файл устарел и был очищен.

### База слишком большая

Очистить старые данные:

```bash
srvmon cleanup
```

Уменьшить срок хранения:

```toml
[storage]
retention_days = 14
```

## Перенос На Другой ПК

### Вариант 1: перенести только проект

На старом ПК скопируйте папку проекта `srvmon`.

На новом ПК:

```bash
cd srvmon
python -m venv .venv
```

Windows:

```powershell
.venv\Scripts\activate
pip install -e .
srvmon doctor
srvmon live
```

Linux/macOS:

```bash
source .venv/bin/activate
pip install -e .
srvmon doctor
srvmon live
```

### Вариант 2: перенести проект вместе с историей

Скопируйте:

```text
папка проекта srvmon
~/.srvmon/config.toml
~/.srvmon/data/metrics.sqlite3
```

На новом ПК положите:

```text
~/.srvmon/config.toml
~/.srvmon/data/metrics.sqlite3
```

Проверьте конфиг:

```toml
[storage]
database_path = "~/.srvmon/data/metrics.sqlite3"
```

Затем:

```bash
pip install -e .
srvmon doctor
srvmon status
srvmon report -1d
```

### Вариант 3: перенести только БД

Скопируйте файл:

```text
~/.srvmon/data/metrics.sqlite3
```

И используйте его явно:

```bash
srvmon report -1w --storage-path /path/to/metrics.sqlite3
srvmon export --format html -1w --storage-path /path/to/metrics.sqlite3
```

### Что Не Нужно Переносить

Обычно не нужно переносить:

```text
~/.srvmon/srvmon.pid
~/.srvmon/srvmon.log
~/.srvmon/srvmon-web.log
```

PID-файл относится к старому процессу на старом ПК.

### Чеклист После Переноса

```bash
srvmon doctor
srvmon status
srvmon cleanup
srvmon report -1d
srvmon live
```

Если веб не открылся:

```bash
srvmon live --web-port 9000
```

## Рекомендуемый Workflow

Для постоянного мониторинга:

```bash
srvmon start
srvmon status
```

Для просмотра в реальном времени:

```bash
srvmon live
```

Для ежедневного отчета:

```bash
srvmon report -1d
srvmon export --format html -1d
```

Для проверки проблем:

```bash
srvmon doctor
```

