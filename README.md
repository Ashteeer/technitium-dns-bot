# Technitium DNS spoofing bot

[![CI](https://github.com/Ashteeer/technitium-dns-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/Ashteeer/technitium-dns-bot/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

Бот для Ubuntu, управляющий **подменой DNS-ответов** в [Technitium DNS Server](https://technitium.com/dns/)
через его HTTP API. Подмена реализована через авторитативные зоны (Zones):
для каждого домена создаётся Primary-зона с A/AAAA-записями на нужный IP.

Источники правил:

1. **Интернет-списки** (JSON) — скачиваются по расписанию.
2. **Правила пользователя** через Telegram-бота — имеют наивысший приоритет.

## 🚀 Быстрый старт

Установка на сервере Ubuntu **одной командой** (зависимости + бот + systemd-сервис
+ системная команда `ttbot`):

```bash
curl -fsSL https://raw.githubusercontent.com/Ashteeer/technitium-dns-bot/main/INSTALL.sh | sudo bash
```

<details>
<summary>Альтернатива через <code>wget</code></summary>

```bash
wget -qO- https://raw.githubusercontent.com/Ashteeer/technitium-dns-bot/main/INSTALL.sh | sudo bash
```
</details>

Установщик спросит Telegram ID администратора и токен бота, затем покажет сводку.
Конкретная версия — `... | sudo bash -s -- 1.0.0`. Подробности, ручная установка и
управление — в разделе [Установка](#установка-ubuntu) и [docs/DEPLOY.md](docs/DEPLOY.md).

## Возможности

- Обновление подмены из списков по интервалу. Добавляются **только новые** домены;
  пропавшие из источника домены **не удаляются** (как требовалось).
- Правила пользователя приоритетнее списков. Заблокированный пользователем домен
  **никогда** не будет подменяться из списка — пока пользователь не уберёт правило.
- Telegram-UI на кнопках: добавить домен, удалить домен, настройки
  (просмотр правил с номерами, удаление правила по номеру).
- Whitelist по Telegram ID.
- Поддержка нескольких IP (IPv4 → A, IPv6 → AAAA).

## Wildcard и точное совпадение (`@`)

- **Из списков** домены всегда добавляются как *wildcard*: подменяется сам домен
  и все его поддомены (`example.com` и `*.example.com`).
- **Из Telegram**:
  - `example.com` — wildcard (домен + поддомены);
  - `@example.com` — только сам домен (без поддоменов).
- **IDN-домены** (`сайт.рф` и т.п.) автоматически приводятся к punycode
  (`xn--80aswg.xn--p1ai`) — и при вводе в боте, и при разборе списков.

### Логика приоритетов (что реально применяется к зоне)

Для каждого домена вычисляется `(apex, wildcard)` — подменять ли сам домен и поддомены:

| Источник правила            | apex | wildcard |
|-----------------------------|:----:|:--------:|
| домен есть в списке          | ✅   | ✅       |
| user **add** `domain`        | ✅   | ✅       |
| user **add** `@domain`       | ✅   | ❌       |
| user **block** `domain`      | ❌   | ❌       |
| user **block** `@domain`     | ❌   | как в списке |

Пользовательское правило всегда переопределяет список для своего домена.

## Формат списков (только JSON)

```json
{
  "version": 1,
  "rules": [
    { "domain_suffix": ["domain.com", "ads.example.net"] }
  ]
}
```

Читается `domain_suffix` (массив строк). Дополнительно поддерживается ключ `domain`.
Пример — [examples/sample-list.json](examples/sample-list.json).

## Установка (Ubuntu)

### Быстрая установка (рекомендуется)

Одной командой — ставит зависимости, последнюю версию с GitHub, systemd-сервис
и системную команду `ttbot`:

```bash
curl -fsSL https://raw.githubusercontent.com/Ashteeer/technitium-dns-bot/main/INSTALL.sh | sudo bash
```

При первой установке скрипт спросит **Telegram ID администратора** (whitelist) и
**токен бота** (можно пропустить, заполнив позже), затем покажет сводку:

```
===============================
Bot ID: 7000000123
Admin ID: 111222333
Config Path: /opt/technitium-bot/config.yaml
===============================
```

Допишите остальные поля и запустите:

```bash
sudo nano /opt/technitium-bot/config.yaml   # spoof_ips, technitium, blocklists
sudo systemctl start technitium-bot
ttbot status
```

Конкретную версию: `... | sudo bash -s -- 1.0.0`. Подробности — в [docs/DEPLOY.md](docs/DEPLOY.md).

### Управление сервисом (команда `ttbot`)

```bash
ttbot --version          # установленная версия
ttbot --update           # обновить до последней версии с GitHub
ttbot --update 1.0.0     # обновить/откатить на конкретную версию (тег 1.0.0)
ttbot restart            # перезапуск
ttbot status             # статус
ttbot logs               # логи (follow)
```

### Ручная установка / разработка

```bash
git clone https://github.com/Ashteeer/technitium-dns-bot.git
cd technitium-dns-bot
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"          # рантайм + dev-инструменты (pytest, ruff, mypy)

cp examples/config.example.yaml config.yaml   # затем отредактируйте
python -m ttbot --config config.yaml --log-level INFO
```

> Требуется Python **3.10+**. Источник истины для зависимостей —
> `pyproject.toml`; `requirements.txt` держится синхронным с ним.

## Конфигурация

См. подробные комментарии в [examples/config.example.yaml](examples/config.example.yaml). Ключевые поля:

- `spoof_ips` — список IP, на которые подменяем (IPv4 и/или IPv6).
- `update_interval` — `6h`, `30m` или `1h30m`.
- `technitium.url` — `http://IP:PORT` панели Technitium (порт по умолчанию `5380`).
- `technitium.token` — **API Token**: в панели Technitium →
  *Administration → Sessions → Create Token*.
- `telegram.bot_token` — токен от [@BotFather](https://t.me/BotFather).
- `telegram.whitelist` — список разрешённых Telegram ID (узнать свой ID можно у
  [@userinfobot](https://t.me/userinfobot)).
- `blocklists` — список источников (имя + URL).

## Telegram: интерфейс

`/start` (или `/menu`) открывает меню:

- **➕ Добавить домен** → отправьте `example.com` (wildcard) или `@example.com` (точно).
- **➖ Удалить домен** → домен больше не будет подменяться.
- **🔍 Проверить домен** → опрашивает **фактические зоны Technitium** и показывает,
  подменяется ли сам домен (и на какой IP), а также перечисляет проксируемые
  **поддомены** запроса. Пример: для `google.com` (когда проксируется только
  `test.google.com`):

  ```
  🔍 Проверка: google.com
  ❌ google.com — не проксируется

  Проксируемые поддомены:
  • *.test.google.com — проксируется на 10.0.0.53
  ```
- **⚙️ Настройки**
  - **📋 Просмотреть правила** — список пользовательских правил с номерами.
  - **🗑 Убрать правило** — удалить правило по номеру (домен вернётся к поведению
    по умолчанию: снова подменяется, если присутствует в списках).
  - **🌐 Сменить IP подмены** — показывает текущий IP и принимает новый (один или
    несколько через пробел/запятую, с проверкой корректности). Новый IP заменяет
    старый и сразу применяется ко **всем** уже подменяемым доменам — зоны
    пересоздаются на новый адрес.

## Состояние

Файл `state_file` (по умолчанию `state.json`) хранит пользовательские правила,
«липкое» множество доменов из списков и текущие IP подмени (`spoof_ipv4`/
`spoof_ipv6`). Запись атомарна (через временный файл).

> **Источник истины для IP подмены.** При первом запуске IP берутся из
> `config.yaml` (`spoof_ips`) и сохраняются в `state.json`. После смены IP через
> бота приоритет у `state.json`. Чтобы снова взять IP из конфига, удалите ключи
> `spoof_ipv4`/`spoof_ipv6` из `state.json`.

## Важные замечания

- **Зоны перезаписываются.** Бот создаёт Primary-зону на каждый подменяемый домен.
  Если в Technitium уже есть зона с тем же именем (например, ваша внутренняя),
  при применении она будет пересоздана. Не указывайте в подмену домены, для которых
  у вас есть собственные зоны.
- **Большие списки.** Подход «зона на домен» рассчитан на *подмену* (redirect на свой
  IP). Для списков в сотни тысяч доменов первичная синхронизация создаёт столько же
  зон и может занять время (запросы идут с ограничением `max_concurrency`). Если нужна
  именно блокировка (а не редирект на конкретный IP), эффективнее встроенная функция
  Blocking в Technitium. Этот бот сознательно использует Zones, как и требовалось.
- **Безопасность.** Токены и `state.json` дают контроль над DNS — храните файл
  конфигурации с правами `600` и не публикуйте. `whitelist` обязателен и не может
  быть пустым.

## Разработка и тесты

```bash
pip install -e ".[dev]"   # установка с dev-зависимостями (pytest, ruff, mypy)

pytest                    # офлайн-тесты ядра (сеть и Technitium не нужны)
ruff check .              # линт
ruff format --check .     # проверка форматирования
mypy src/ttbot            # статическая типизация
```

Тесты покрывают разбор интервала и конфига, нормализацию доменов и IDN,
`@`/wildcard, парсинг списков, логику приоритетов, `check_domain` и
персистентность состояния. Те же проверки прогоняются в CI (GitHub Actions,
матрица Python 3.10–3.13).

## Структура проекта

```
technitium-dns-bot/
├── pyproject.toml          # метаданные пакета, зависимости, конфиг ruff/mypy/pytest
├── requirements.txt        # runtime-зависимости для деплоя (синхронны с pyproject)
├── INSTALL.sh              # установка/обновление на сервере (по числовым тегам)
├── LICENSE                 # MIT
├── README.md
├── CLAUDE.md               # техническая карта проекта
├── src/ttbot/              # пакет (src-layout)
│   ├── __main__.py         # точка входа, планировщик, жизненный цикл
│   ├── config.py           # загрузка/валидация конфига, разбор интервала
│   ├── domains.py          # нормализация доменов, @/wildcard, IDN→punycode
│   ├── lists.py            # скачивание и парсинг JSON-списков
│   ├── state.py            # персистентное состояние (правила, списки, IP подмены)
│   ├── technitium.py       # асинхронный клиент Technitium API
│   ├── reconciler.py       # логика приоритетов, синхронизация, смена IP
│   └── bot.py              # Telegram-UI на кнопках
├── tests/                  # pytest-набор (офлайн)
├── scripts/
│   ├── ttbot               # системная команда управления (--version/--update/…)
│   └── config_tool.py      # init/show config для INSTALL.sh
├── deploy/
│   └── technitium-bot.service   # unit для systemd
├── examples/
│   ├── config.example.yaml # пример конфигурации
│   └── sample-list.json    # пример списка
├── docs/
│   ├── DEPLOY.md           # развёртывание и эксплуатация
│   └── RELEASE.md          # процесс релиза
└── .github/workflows/ci.yml  # CI: ruff + mypy + pytest
```
