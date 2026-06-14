# DEPLOY.md — развёртывание Technitium DNS spoofing bot

Руководство по установке и эксплуатации бота на сервере Ubuntu.
Все команды выполняются от `root` или через `sudo`.

---

## 0. Что понадобится заранее

- **Ubuntu** 22.04 / 24.04 (или совместимая), Python 3.10+.
- Установленный и работающий **Technitium DNS Server** с доступным HTTP API.
- **API-токен Technitium**: панель → *Administration* → *Sessions* →
  *Create Token*.
- **Telegram-бот**: создайте у [@BotFather](https://t.me/BotFather), получите
  токен.
- **Свой Telegram ID**: узнайте у [@userinfobot](https://t.me/userinfobot)
  (число — оно пойдёт в whitelist).

---

## Быстрая установка (одной командой)

Если нужен типовой деплой в `/opt/technitium-bot` под пользователем `ttbot` —
используйте `INSTALL.sh`: он ставит зависимости, последнюю версию с GitHub,
systemd-сервис и команду `ttbot`.

```bash
curl -fsSL https://raw.githubusercontent.com/Ashteeer/technitium-dns-bot/main/INSTALL.sh | sudo bash
#   конкретная версия:  ... | sudo bash -s -- 1.0.0

sudo nano /opt/technitium-bot/config.yaml    # заполнить (см. §4)
sudo systemctl start technitium-bot
ttbot status
```

Дальнейшее управление и обновление — командой `ttbot` (см. §8). Разделы 1–7
ниже описывают то же самое вручную (если нужен контроль над шагами).

---

## 1. Подготовка системы

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
```

---

## 2. Размещение проекта

```bash
sudo mkdir -p /opt/technitium-bot
sudo git clone https://github.com/Ashteeer/technitium-dns-bot.git /opt/technitium-bot
# конкретная версия:  sudo git -C /opt/technitium-bot checkout 1.0.0

cd /opt/technitium-bot
ls    # должны быть: src/ pyproject.toml requirements.txt examples/ deploy/ и т.д.
```

---

## 3. Виртуальное окружение и зависимости

```bash
cd /opt/technitium-bot
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install .
# (ставит пакет + зависимости из pyproject; при src-layout это обязательно,
#  иначе `python -m ttbot` не найдёт пакет)
```

> **Требуется Python 3.10+.** На Ubuntu 22.04 это 3.10, на 24.04 — 3.12.

> **Важно:** каталог окружения называется `venv` (именно так указан путь в
> systemd-юните: `/opt/technitium-bot/venv/bin/python`). Если создадите `.venv`,
> либо переименуйте, либо поправьте `ExecStart` в юните.

Проверка, что всё импортируется:

```bash
./venv/bin/python -c "import ttbot; print('ttbot', ttbot.__version__)"
```

---

## 4. Конфигурация

```bash
cd /opt/technitium-bot
cp examples/config.example.yaml config.yaml
nano config.yaml
```

Заполните ключевые поля:

```yaml
spoof_ips:
  - "10.0.0.53"          # IP(ы), на которые подменяем (можно v4 и v6)

update_interval: "6h"     # как часто обновлять списки: "6h", "30m", "1h30m"

technitium:
  url: "http://127.0.0.1:5380"   # адрес API Technitium
  token: "ВАШ_API_ТОКЕН"

telegram:
  bot_token: "ТОКЕН_ОТ_BOTFATHER"
  whitelist:
    - 123456789          # ВАШ Telegram ID (без него бот никого не пустит)

blocklists:
  - name: "Основной список"
    url: "https://raw.githubusercontent.com/USER/REPO/main/list.json"

state_file: "state.json"
```

> **GitHub-ссылки** должны вести на **raw**-файл
> (`raw.githubusercontent.com/.../main/file.json`), а не на страницу `/blob/`.
> Иначе вернётся HTML и список не загрузится.

---

## 5. Отдельный системный пользователь

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin ttbot
sudo chown -R ttbot:ttbot /opt/technitium-bot
```

> Запускать под отдельным пользователем без shell безопаснее, чем под root.

---

## 6. Первый запуск вручную (проверка)

Перед установкой как сервис — убедитесь, что бот стартует:

```bash
cd /opt/technitium-bot
sudo -u ttbot ./venv/bin/python -m ttbot --config config.yaml --log-level INFO
```

В логе ожидаемо:

```
... ttbot: Запуск бота…
... ttbot: Technitium доступен (http://127.0.0.1:5380), зон на сервере: N
... ttbot: Планировщик: обновление списков каждые 6h (21600 сек).
... telegram.ext.Application: Application started
```

Откройте бота в Telegram, отправьте `/start` — должно появиться меню.
Остановите проверочный запуск: `Ctrl+C`.

---

## 7. Установка как systemd-сервис

```bash
sudo cp /opt/technitium-bot/deploy/technitium-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now technitium-bot
sudo systemctl status technitium-bot
```

Просмотр логов:

```bash
journalctl -u technitium-bot -f          # в реальном времени
journalctl -u technitium-bot --since "10 min ago"
```

---

## 8. Обновление бота

### Командой `ttbot` (если ставили через INSTALL.sh)

```bash
ttbot --version          # текущая версия
sudo ttbot --update      # обновить до последней версии с GitHub
sudo ttbot --update 1.0.0  # установить/откатить конкретную версию (тег 1.0.0)
```

`ttbot --update` сам качает свежий `INSTALL.sh`, обновляет код и зависимости,
**сохраняя** `config.yaml` и `state.json`, и перезапускает сервис.

### Перекачать списки / сбросить правила

```bash
sudo ttbot --reload      # перекачать списки и заново применить все правила
sudo ttbot --flush       # удалить ВСЕ правила и зоны, сбросить состояние (спросит подтверждение; -y чтобы без него)
```

`--reload`/`--flush` останавливают сервис, выполняют операцию и снова запускают.
`--reload` также доступен в боте: Настройки → 🔄 Обновить списки.

### Переход на Technitium App (`manage_zones: false`)

Когда DNS-ответы обслуживает ваш Technitium App (читает `rules.yaml`):

```bash
sudo ttbot --update                              # 1. обновить бота
sudo sed -i 's/^manage_zones:.*/manage_zones: false/' /opt/technitium-bot/config.yaml  # 2. (или вручную)
sudo ttbot restart                               # 3. бот в режиме App — зоны не трогает
sudo ttbot --cleanup-zones                       # 4. удалить старые зоны (правила сохранятся)
```

После этого Technitium перестаёт быть авторитативным по этим доменам — отвечает
App. Бот продолжает обновлять `rules.yaml` из списков и Telegram; `technitium`-креды
нужны только для разового `--cleanup-zones`.

### Вручную (git-clone установка)

```bash
sudo systemctl stop technitium-bot
sudo git -C /opt/technitium-bot fetch --tags
sudo git -C /opt/technitium-bot checkout 1.0.0     # или origin/main
sudo -u ttbot /opt/technitium-bot/venv/bin/pip install /opt/technitium-bot
sudo systemctl start technitium-bot
sudo systemctl status technitium-bot
```

---

## 9. Смена IP подмены

Когда у сервера сменился IP (а вы заворачиваете трафик на него).

### Способ 1 (рекомендуемый): из Telegram-бота

Откройте бота → **⚙️ Настройки → 🌐 Сменить IP подмены**, отправьте новый IP
(можно несколько через пробел/запятую). Бот проверит адрес, сохранит его в
`state.json` и сразу пересоздаст все управляемые зоны на новый IP. Перезапуск
сервиса и правка конфига не нужны.

> После смены IP через бота источник истины — `state.json`. Значение `spoof_ips`
> в `config.yaml` для IP больше не применяется (пока не удалить ключи
> `spoof_ipv4`/`spoof_ipv6` из `state.json`).

### Способ 2 (вручную, без бота)

```bash
# 1. Поменять IP в конфиге
sudo nano /opt/technitium-bot/config.yaml      # обновить spoof_ips

# 2. Чтобы СТАРЫЕ зоны пересоздались с новым IP — обнулить list_domains и
#    удалить сохранённые IP (иначе выиграет state.json, а не конфиг).
#    user_rules сохранятся и применятся заново автоматически.
sudo systemctl stop technitium-bot

sudo -u ttbot python3 - <<'EOF'
import json
path = "/opt/technitium-bot/state.json"   # совпадает с state_file в конфиге
with open(path) as f:
    data = json.load(f)
print("Доменов в списках:", len(data.get("list_domains", [])))
print("Пользовательских правил:", len(data.get("user_rules", [])))
data["list_domains"] = []                  # сброс — пересоздадутся при sync
data.pop("spoof_ipv4", None)               # убрать сохранённые IP, чтобы
data.pop("spoof_ipv6", None)               # IP пере-сидировался из config.yaml
with open(path, "w") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print("Готово: list_domains и сохранённые IP сброшены, user_rules сохранены.")
EOF

# 3. Запустить — через ~10 сек начнётся синхронизация и пересоздание зон
sudo systemctl start technitium-bot
journalctl -u technitium-bot -f
```

> Пользовательские правила применяются ботом при их пересоздании, так что они
> тоже получат новый IP. Чисто списочные домены пересоздаются благодаря сбросу
> `list_domains`.

---

## 10. Резервная копия / перенос состояния

Всё состояние — в одном файле `state.json`:

```bash
sudo cp /opt/technitium-bot/state.json /opt/technitium-bot/state.json.bak
```

Для переноса на другой сервер достаточно скопировать `config.yaml` и
`state.json`. Зоны в Technitium восстановятся при первой синхронизации.

---

## 11. Типичные ошибки

| Симптом в `systemctl status` / логах | Причина | Решение |
|---|---|---|
| `status=217/USER` | нет пользователя `ttbot` | создать (см. §5) или убрать `User=`/`Group=` из юнита |
| `status=203/EXEC` | неверный путь к python | проверить `ExecStart`; путь должен быть `venv`, а не `.venv` |
| `невалидный JSON: Expecting value` | ссылка ведёт на HTML-страницу GitHub | заменить `/blob/` на `raw.githubusercontent.com` |
| `Не удалось связаться с Technitium API` | неверный URL/токен или API недоступен | проверить `technitium.url` и `token`, доступность панели |
| Бот молчит / «Доступ запрещён» | вашего ID нет в whitelist | добавить свой Telegram ID в `telegram.whitelist` |

---

## 12. Удаление

```bash
sudo systemctl disable --now technitium-bot
sudo rm /etc/systemd/system/technitium-bot.service
sudo systemctl daemon-reload
sudo rm -rf /opt/technitium-bot
sudo userdel ttbot
```

> Созданные в Technitium зоны при этом **не удаляются** — уберите их вручную в
> панели Technitium, если они больше не нужны.
