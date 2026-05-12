# srvmon

Textual TUI для просмотра серверных метрик: CPU, память, диски, сеть, процессы,

## Установка и запуск

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
srvmon live
```

На Linux используйте:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
srvmon live
```

По умолчанию история метрик пишется в SQLite:

```text
~/.srvmon/data/metrics.sqlite3
```

Записи старше 62 дней автоматически удаляются при очередной записи. Интервал
сбора в хранилище совпадает со скоростью live-обновления.
В историю сохраняются load average, CPU, RAM/swap, usage всех дисков, disk I/O,
network in/out в MB/s, network errors/drops и top-10 процессов по CPU/RAM.

```bash
srvmon live
srvmon live -0.5s
srvmon live -5s
srvmon live --storage-path /opt/srvmon/metrics.sqlite3
srvmon live --no-storage
```

Допустимый интервал live-обновления: от 0.5 до 10 секунд. Если указать значение
за пределами диапазона, srvmon покажет предупреждение и возьмет ближайшее
разрешенное значение.

## Отчеты

```bash
srvmon report -1h
srvmon report -1d
srvmon report -1w
srvmon report -1m
srvmon report -2m
```

Если период не указан, используется `-1d`. Отчет читает локальную SQLite-историю
и показывает изменения, средние и пиковые значения для load average, CPU,
RAM/swap, disk usage/I/O, network traffic, errors/drops и top-10 процессов по
CPU/RAM за выбранный период. Для альтернативной БД:

```bash
srvmon report -1w --storage-path /opt/srvmon/metrics.sqlite3
```

## Команды

```bash
srvmon live
srvmon live -0.5s
srvmon live --no-web
srvmon start
srvmon stop
srvmon report -1d
srvmon export --format json -1d
srvmon export --format csv -1m --output metrics.csv
srvmon export --format html -1w --output report.html
srvmon status
srvmon doctor
srvmon cleanup
```

`status` показывает быстрый текущий обзор с теми же цветовыми правилами, что
`live` и `report`. `cleanup` удаляет записи старше 62 дней.

`start` запускает фоновый сбор метрик как отдельный процесс, а `stop`
останавливает его. Агент использует `collection_seconds` из
`~/.srvmon/config.toml`, пишет данные в SQLite и хранит PID в:

```text
~/.srvmon/srvmon.pid
```

Лог фонового процесса:

```text
~/.srvmon/srvmon.log
```

## Веб-Дашборд

`srvmon live` автоматически запускает локальную веб-версию рядом с TUI и
пытается открыть браузер:

```text
http://127.0.0.1:8765
```

Если порт занят, будет выбран ближайший свободный порт. Веб-дашборд полностью
локальный: HTML, CSS и JavaScript отдаются самим srvmon без внешних CDN.

Возможности:

- графики истории CPU, RAM, Network и Disk I/O
- таблица текущих top-процессов
- состояние дисков
- выбор периода: `-1h`, `-1d`, `-1w`, `-1m`, `-2m`
- кнопки экспорта JSON/CSV/HTML за выбранный период

Опции:

```bash
srvmon live --web-port 9000
srvmon live --no-web
```

## Экспорт

```bash
srvmon export --format json -1d
srvmon export --format csv -1m --limit 1000 --output metrics.csv
srvmon export --format html -1w --output report.html
```

Форматы:

- `json` — структурированные `samples`, `disk_usage`, `top_processes`
- `csv` — плоская таблица `metric_samples`
- `html` — самодостаточный HTML-отчёт с таблицами

`--limit` ограничивает количество экспортируемых sample-записей.


## Active Alerts

`srvmon live` ? ????????? ???-??????? ?????????? ???????? ?????????????? ????? ?? ??????? ????????. ??? ?????????????? ?? ???????? ????????? CPU, Load, RAM, Swap, Disk ? Network errors/drops, ?????? ?? ???????????? ? ?? ??????? ????????? ???????.

## Health Score

`srvmon status` и `srvmon report` показывают единую оценку:

```text
Health Score: 82/100 | Status: WARNING
```

Оценка строится по состояниям Load, CPU, RAM, Swap, Disk и Network
errors/drops.

## Диагностика

```bash
srvmon doctor
```

Проверяет окружение:

- версию Python
- доступность SQLite
- права на `~/.srvmon`
- доступ к процессам
- поддержку temperature/fan sensors через `psutil`

## Anomaly Detection И Глубокие Отчеты

`srvmon report` теперь дополнительно показывает:

- аномалии по скользящей статистике и z-score
- сравнение текущих значений с типичным поведением за последние дни
- сообщения вида `Network out is above typical behavior by 240%`
- самый тяжелый CPU/RAM процесс за период
- пиковую нагрузку и пиковый CPU
- рост использования дисков
- сколько времени система была в WARN/CRIT
- рекомендации по оптимизации

## Конфигурация

При запуске srvmon пытается создать и прочитать:

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

Пустые `disks` и `interfaces` означают “мониторить всё доступное”. Для
ограничения можно указать, например:

```toml
[monitoring]
disks = ["/", "/var"]
interfaces = ["eth0"]
```

На Windows диски можно указывать как `["C:\\"]`, а интерфейсы по имени из
системы. В SQLite автоматически создаётся таблица `srvmon_config`, куда
сохраняется активная конфигурация: путь к БД, интервалы, пороги и выбранные
диски/интерфейсы.

## Цветовые пороги

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

Disk I/O throughput и Network throughput считаются информационными метриками:
универсального критического порога без знания дисков и канала нет, поэтому
критичность для них определяется latency и errors/drops.

Некоторые метрики зависят от ОС и прав пользователя:

- inodes, systemd, sensors, fan speed лучше всего работают на Linux;
- latency дисков не все ОС отдают напрямую, поэтому поле может быть `n/a`.
