#!/usr/bin/env bash
# ============================================================================
#  Technitium DNS spoofing bot — установка и обновление (Ubuntu 22.04 / 24.04+)
#
#  Использование (от root):
#     sudo ./INSTALL.sh            # установить/обновить до последней версии (тег)
#     sudo ./INSTALL.sh 1.2.0      # установить конкретную версию (тег 1.2.0)
#
#  Можно запускать прямо из сети:
#     curl -fsSL https://raw.githubusercontent.com/Ashteeer/technitium-dns-bot/main/INSTALL.sh | sudo bash
#     curl -fsSL .../INSTALL.sh | sudo bash -s -- 1.2.0
#
#  Скрипт идемпотентен: при повторном запуске обновляет код и зависимости,
#  СОХРАНЯЯ config.yaml и state.json.
# ============================================================================
set -euo pipefail

# ----------------------------------------------------------------- константы
REPO="Ashteeer/technitium-dns-bot"
INSTALL_DIR="/opt/technitium-bot"
SERVICE="technitium-bot"
RUN_USER="ttbot"
MANAGE_CMD="ttbot"                       # системная команда -> /usr/local/bin/ttbot
VENV="$INSTALL_DIR/venv"
PY="$VENV/bin/python"

# ----------------------------------------------------------------- утилиты
c_info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
c_ok()   { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
c_warn() { printf '\033[1;33m!\033[0m %s\n' "$*"; }
die()    { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# Чтение из терминала даже при запуске через "curl | sudo bash".
ask_tty() {  # $1 = текст приглашения; печатает ответ в stdout
    local ans=""
    if [ -r /dev/tty ]; then
        printf '%s' "$1" > /dev/tty
        IFS= read -r ans < /dev/tty || ans=""
    fi
    printf '%s' "$ans"
}

[ "$(id -u)" -eq 0 ] || die "Запустите от root: sudo ./INSTALL.sh [версия]"

REQ_VERSION="${1:-}"

# ----------------------------------------------------- 1. системные пакеты
c_info "Установка системных зависимостей (python3, venv, pip, git, curl)…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl ca-certificates >/dev/null
c_ok "Системные пакеты готовы."

# ----------------------------------------------------- 2. определить версию
resolve_latest_tag() {
    git ls-remote --tags --refs "https://github.com/$REPO.git" 2>/dev/null \
        | awk -F/ '{print $NF}' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+' | sort -V | tail -n1
}

if [ -n "$REQ_VERSION" ]; then
    TAG="$REQ_VERSION"                    # релизы называются числами: 1.2.0
    c_info "Запрошена версия: $TAG"
else
    TAG="$(resolve_latest_tag || true)"
    if [ -n "$TAG" ]; then
        c_info "Последняя версия по тегам: $TAG"
    else
        c_warn "Теги не найдены — ставлю ветку по умолчанию."
    fi
fi

# ----------------------------------------------------- 3. скачать код
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
c_info "Загрузка кода из https://github.com/$REPO…"
if [ -n "$TAG" ]; then
    git clone --quiet --depth 1 --branch "$TAG" "https://github.com/$REPO.git" "$TMP/src" \
        || die "Не удалось склонировать версию $TAG (проверьте, что она существует)."
else
    git clone --quiet --depth 1 "https://github.com/$REPO.git" "$TMP/src" \
        || die "Не удалось склонировать репозиторий."
fi
rm -rf "$TMP/src/.git"
INSTALLED_VERSION="$(git -C "$TMP/src" describe --tags 2>/dev/null || echo "${TAG:-dev}")"
c_ok "Код получен ($INSTALLED_VERSION)."

# ----------------------------------------------------- 4. разместить файлы
FRESH_INSTALL=0
[ -f "$INSTALL_DIR/config.yaml" ] || FRESH_INSTALL=1

mkdir -p "$INSTALL_DIR"
rm -rf "$INSTALL_DIR/src" "$INSTALL_DIR/ttbot"   # чистим старый код (вкл. legacy-layout)
cp -a "$TMP/src/." "$INSTALL_DIR/"               # config.yaml/state.json в repo нет — не затрём
c_ok "Файлы размещены в $INSTALL_DIR."

# ----------------------------------------------------- 5. venv + пакет
c_info "Создание venv и установка пакета…"
[ -d "$VENV" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet "$INSTALL_DIR"
c_ok "Зависимости установлены."

# ----------------------------------------------------- 6. первичная настройка
if [ "$FRESH_INSTALL" -eq 1 ]; then
    echo
    c_info "Первичная настройка (Enter — оставить пустым):"
    ADMIN_ID="$(ask_tty '  Telegram ID администратора (whitelist): ')"
    BOT_TOKEN="$(ask_tty '  Токен бота от @BotFather: ')"
    TTBOT_ADMIN_ID="$ADMIN_ID" TTBOT_BOT_TOKEN="$BOT_TOKEN" \
        "$PY" "$INSTALL_DIR/scripts/config_tool.py" init \
        "$INSTALL_DIR/examples/config.example.yaml" "$INSTALL_DIR/config.yaml"
    c_warn "Создан config.yaml. Заполните остальные поля (spoof_ips, technitium, blocklists)."
    c_warn "Полный шаблон с комментариями: $INSTALL_DIR/examples/config.example.yaml"
fi

# ----------------------------------------------------- 7. пользователь
if ! id -u "$RUN_USER" >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$RUN_USER"
    c_ok "Создан системный пользователь $RUN_USER."
fi

# ----------------------------------------------------- 8. systemd + manage
install -m 0644 "$INSTALL_DIR/deploy/$SERVICE.service" "/etc/systemd/system/$SERVICE.service"
install -m 0755 "$INSTALL_DIR/scripts/ttbot" "/usr/local/bin/$MANAGE_CMD"
c_ok "Установлены systemd-юнит и команда '$MANAGE_CMD'."

chown -R "$RUN_USER:$RUN_USER" "$INSTALL_DIR"
chmod 600 "$INSTALL_DIR/config.yaml" 2>/dev/null || true

# ----------------------------------------------------- 9. запуск сервиса
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null 2>&1 || true

if [ "$FRESH_INSTALL" -eq 1 ]; then
    echo
    c_warn "ПЕРВАЯ УСТАНОВКА. Сервис НЕ запущен — допишите config.yaml и запустите:"
    echo "    sudo nano $INSTALL_DIR/config.yaml"
    echo "    sudo systemctl start $SERVICE"
else
    c_info "Перезапуск сервиса…"
    systemctl restart "$SERVICE"
    sleep 1
    systemctl is-active --quiet "$SERVICE" \
        && c_ok "Сервис $SERVICE запущен ($INSTALLED_VERSION)." \
        || c_warn "Сервис не активен — проверьте: sudo $MANAGE_CMD logs"
fi

# ----------------------------------------------------- 10. сводка
echo
"$PY" "$INSTALL_DIR/scripts/config_tool.py" show "$INSTALL_DIR/config.yaml" 2>/dev/null || true
echo
c_ok "Готово. Управление: $MANAGE_CMD --help"
