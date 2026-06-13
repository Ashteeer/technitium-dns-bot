"""Telegram-бот: кнопочный интерфейс управления подменой DNS."""

from __future__ import annotations

import logging
from functools import wraps

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .config import ConfigError, parse_spoof_ips
from .domains import EXACT, describe_scope, normalize_domain, parse_user_input
from .reconciler import CheckReport, Reconciler
from .technitium import TechnitiumError

log = logging.getLogger(__name__)

# Ключи режима ожидания текстового ввода (хранятся в context.user_data).
MODE_ADD = "add"
MODE_BLOCK = "block"
MODE_REMOVE = "remove"
MODE_CHECK = "check"
MODE_CHANGEIP = "changeip"


# ----------------------------------------------------------------- хелперы
def _reconciler(context: ContextTypes.DEFAULT_TYPE) -> Reconciler:
    return context.application.bot_data["reconciler"]


def restricted(func):
    """Пускать только пользователей из whitelist."""

    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *a, **k):
        cfg = context.application.bot_data["config"]
        uid = update.effective_user.id if update.effective_user else None
        if uid not in cfg.telegram.whitelist:
            log.warning("Отклонён доступ для Telegram ID %s", uid)
            if update.callback_query:
                await update.callback_query.answer("Доступ запрещён", show_alert=True)
            elif update.message:
                await update.message.reply_text("⛔ Доступ запрещён.")
            return
        return await func(update, context, *a, **k)

    return wrapper


def main_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("➕ Добавить домен", callback_data="add"),
                InlineKeyboardButton("➖ Удалить домен", callback_data="block"),
            ],
            [InlineKeyboardButton("🔍 Проверить домен", callback_data="check")],
            [InlineKeyboardButton("⚙️ Настройки", callback_data="settings")],
        ]
    )


def settings_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📋 Просмотреть правила", callback_data="view")],
            [InlineKeyboardButton("🗑 Убрать правило", callback_data="remove")],
            [InlineKeyboardButton("🌐 Сменить IP подмены", callback_data="changeip")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="menu")],
        ]
    )


def cancel_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="menu")]])


MENU_TEXT = "🛡 *Technitium DNS — подмена ответов*\n\nВыберите действие:"


def _rules_text(reconciler: Reconciler) -> str:
    rules = reconciler.state.list_rules()
    if not rules:
        body = "_Пользовательских правил нет._"
    else:
        lines = []
        for i, r in enumerate(rules, 1):
            if r.action == "add":
                mark = "✅ подменяется"
            else:
                mark = "⛔ заблокирован"
            prefix = "@" if r.scope == EXACT else ""
            lines.append(f"{i}. {mark} — `{prefix}{r.domain}` ({describe_scope(r.scope)})")
        body = "\n".join(lines)
    extra = f"\n\n_Доменов из списков: {len(reconciler.state.list_domains)}_"
    return "📋 *Пользовательские правила*\n\n" + body + extra


def _current_ips(reconciler: Reconciler) -> str:
    ips = reconciler.state.spoof_ipv4 + reconciler.state.spoof_ipv6
    return ", ".join(ips) if ips else "не заданы"


def _scope_note(apex: bool, wildcard: bool) -> str:
    if apex and wildcard:
        return "домен и поддомены"
    if apex:
        return "только домен"
    return "только поддомены"


def _format_check(report: CheckReport) -> str:
    lines = [f"🔍 Проверка: `{report.query}`", ""]
    if report.proxied:
        ip = ", ".join(report.ips) or "—"
        extra = f"  _({report.reason})_" if report.reason else ""
        lines.append(f"✅ `{report.query}` — проксируется на `{ip}`{extra}")
    else:
        lines.append(f"❌ `{report.query}` — не проксируется")
    if report.subdomains:
        lines.append("")
        lines.append("*Проксируемые поддомены:*")
        for hit in report.subdomains:
            name = hit.domain if hit.apex else f"*.{hit.domain}"
            ip = ", ".join(hit.ips) or "—"
            note = _scope_note(hit.apex, hit.wildcard)
            lines.append(f"• `{name}` — проксируется на `{ip}` _({note})_")
    return "\n".join(lines)


# ----------------------------------------------------------------- команды
@restricted
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("mode", None)
    await update.message.reply_text(
        MENU_TEXT, reply_markup=main_menu_markup(), parse_mode="Markdown"
    )


# ------------------------------------------------------------- кнопки
@restricted
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "menu":
        context.user_data.pop("mode", None)
        await query.edit_message_text(
            MENU_TEXT, reply_markup=main_menu_markup(), parse_mode="Markdown"
        )

    elif data == "add":
        context.user_data["mode"] = MODE_ADD
        await query.edit_message_text(
            "➕ Отправьте домен, который нужно *подменять*.\n\n"
            "• `example.com` — домен и все поддомены (wildcard)\n"
            "• `@example.com` — только сам домен (без поддоменов)",
            reply_markup=cancel_markup(),
            parse_mode="Markdown",
        )

    elif data == "block":
        context.user_data["mode"] = MODE_BLOCK
        await query.edit_message_text(
            "➖ Отправьте домен, который *никогда не должен подменяться*.\n\n"
            "• `example.com` — домен и все поддомены\n"
            "• `@example.com` — исключить только сам домен (поддомены — по спискам)",
            reply_markup=cancel_markup(),
            parse_mode="Markdown",
        )

    elif data == "settings":
        context.user_data.pop("mode", None)
        await query.edit_message_text(
            "⚙️ *Настройки*", reply_markup=settings_menu_markup(), parse_mode="Markdown"
        )

    elif data == "check":
        context.user_data["mode"] = MODE_CHECK
        await query.edit_message_text(
            "🔍 Введите домен или поддомен для проверки.\n\n"
            "Примеры: `google.com`, `test.google.com`, `sub.example.org`",
            reply_markup=cancel_markup(),
            parse_mode="Markdown",
        )

    elif data == "view":
        await query.edit_message_text(
            _rules_text(_reconciler(context)),
            reply_markup=settings_menu_markup(),
            parse_mode="Markdown",
        )

    elif data == "remove":
        reconciler = _reconciler(context)
        if not reconciler.state.list_rules():
            await query.edit_message_text(
                "🗑 Удалять нечего — пользовательских правил нет.",
                reply_markup=settings_menu_markup(),
                parse_mode="Markdown",
            )
            return
        context.user_data["mode"] = MODE_REMOVE
        await query.edit_message_text(
            _rules_text(reconciler) + "\n\nОтправьте *номер* правила для удаления.",
            reply_markup=cancel_markup(),
            parse_mode="Markdown",
        )

    elif data == "changeip":
        context.user_data["mode"] = MODE_CHANGEIP
        await query.edit_message_text(
            f"🌐 *Смена IP подмены*\n\nТекущий IP: `{_current_ips(_reconciler(context))}`\n\n"
            "Отправьте новый IP, на который перенаправлять домены.\n"
            "Можно несколько через пробел/запятую (IPv4 → A, IPv6 → AAAA).\n\n"
            "⚠️ Новый IP заменит текущий и будет применён ко *всем* уже "
            "подменяемым доменам (пересоздание зон может занять время).",
            reply_markup=cancel_markup(),
            parse_mode="Markdown",
        )


# ------------------------------------------------------------- текстовый ввод
@restricted
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    mode = context.user_data.get("mode")
    text = (update.message.text or "").strip()
    reconciler = _reconciler(context)

    if mode in (MODE_ADD, MODE_BLOCK):
        parsed = parse_user_input(text)
        if parsed is None:
            await update.message.reply_text(
                "⚠️ Не похоже на корректный домен. Попробуйте ещё раз "
                "(например `example.com` или `@example.com`).",
                reply_markup=cancel_markup(),
                parse_mode="Markdown",
            )
            return
        domain, scope = parsed
        try:
            if mode == MODE_ADD:
                await reconciler.user_add(domain, scope)
                verb = "будет подменяться"
            else:
                await reconciler.user_block(domain, scope)
                verb = "больше не будет подменяться"
        except TechnitiumError as e:
            await update.message.reply_text(f"❌ Ошибка Technitium: {e}")
            context.user_data.pop("mode", None)
            return
        context.user_data.pop("mode", None)
        prefix = "@" if scope == EXACT else ""
        await update.message.reply_text(
            f"✅ `{prefix}{domain}` — {verb} ({describe_scope(scope)}).",
            reply_markup=main_menu_markup(),
            parse_mode="Markdown",
        )

    elif mode == MODE_REMOVE:
        if not text.isdigit():
            await update.message.reply_text(
                "⚠️ Введите номер правила (число).", reply_markup=cancel_markup()
            )
            return
        try:
            rule = await reconciler.remove_rule(int(text))
        except TechnitiumError as e:
            await update.message.reply_text(f"❌ Ошибка Technitium: {e}")
            context.user_data.pop("mode", None)
            return
        context.user_data.pop("mode", None)
        if rule is None:
            await update.message.reply_text(
                "⚠️ Правила с таким номером нет.", reply_markup=main_menu_markup()
            )
        else:
            await update.message.reply_text(
                f"🗑 Правило для `{rule.domain}` удалено.",
                reply_markup=main_menu_markup(),
                parse_mode="Markdown",
            )

    elif mode == MODE_CHECK:
        checked = normalize_domain(text)
        if checked is None:
            await update.message.reply_text(
                "⚠️ Не похоже на корректный домен. Попробуйте ещё раз "
                "(например `google.com` или `test.google.com`).",
                reply_markup=cancel_markup(),
                parse_mode="Markdown",
            )
            return
        context.user_data.pop("mode", None)
        try:
            report = await reconciler.check_domain(checked)
        except TechnitiumError as e:
            await update.message.reply_text(
                f"❌ Ошибка Technitium: {e}", reply_markup=main_menu_markup()
            )
            return
        await update.message.reply_text(
            _format_check(report),
            reply_markup=main_menu_markup(),
            parse_mode="Markdown",
        )

    elif mode == MODE_CHANGEIP:
        try:
            ipv4, ipv6 = parse_spoof_ips(text)
        except ConfigError as e:
            await update.message.reply_text(
                f"⚠️ {e}\nВведите корректный IP (например `10.0.0.53` или `10.0.0.53, fd00::53`).",
                reply_markup=cancel_markup(),
                parse_mode="Markdown",
            )
            return
        context.user_data.pop("mode", None)
        new_ips = ", ".join(ipv4 + ipv6)
        n = len(reconciler.managed_domains())
        progress = await update.message.reply_text(
            f"⏳ Меняю IP подмены на `{new_ips}`, пересоздаю зоны ({n} доменов)…",
            parse_mode="Markdown",
        )
        applied, failed = await reconciler.change_spoof_ips(ipv4, ipv6)
        done = f"✅ IP подмены изменён на `{new_ips}`.\nПересоздано зон: *{applied}*"
        if failed:
            done += f", ошибок: *{failed}*"
        await progress.edit_text(done, reply_markup=main_menu_markup(), parse_mode="Markdown")

    else:
        await update.message.reply_text(
            "Используйте /start для вызова меню.", reply_markup=main_menu_markup()
        )


def register_handlers(application: Application) -> None:
    application.add_handler(CommandHandler(["start", "menu"], cmd_start))
    application.add_handler(CallbackQueryHandler(on_button))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
