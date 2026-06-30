import logging
import os
import re
import threading
from datetime import date

from flask import Flask, jsonify
from flask_cors import CORS

from telegram import Update, WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes,
    ConversationHandler, filters
)

import storage

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8532637211:AAH1t6Uez5kXpOUfSJyiAALxrpeWG4AqNPM")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://ezssen.github.io/budget-bot/")
PORT = int(os.environ.get("PORT", 5000))

logging.basicConfig(level=logging.INFO)

# Состояния для ConversationHandler /setup
ASK_INCOME, ASK_SAVINGS_5, ASK_SAVINGS_20, ASK_OBLIGATIONS_CHANGED, ASK_OBLIGATIONS_LIST = range(5)


def fmt(amount):
    """Форматирует число с разделителями тысяч: 478250 -> 478 250"""
    return f"{amount:,.0f}".replace(",", " ")


def main_menu_keyboard():
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(text="📊 Открыть бюджет", web_app=WebAppInfo(url=WEBAPP_URL))]
    ])
    return keyboard


# ---------- /start ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💰",
        reply_markup=main_menu_keyboard()
    )


# ---------- /setup conversation ----------

async def setup_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["setup"] = {}
    await update.message.reply_text(
        "Настройка бюджета 📝\n\nВведи доход за месяц (только число, например 2288250):",
        reply_markup=ReplyKeyboardRemove()
    )
    return ASK_INCOME


async def setup_income(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace(" ", "")
    if not text.isdigit():
        await update.message.reply_text("Нужно просто число, например 2288250. Попробуй ещё раз:")
        return ASK_INCOME

    context.user_data["setup"]["income"] = int(text)
    await update.message.reply_text("Сколько уходит в накопления 5 числа? (число, или 0 если не уходит)")
    return ASK_SAVINGS_5


async def setup_savings_5(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace(" ", "")
    if not text.isdigit():
        await update.message.reply_text("Нужно число, например 515000. Попробуй ещё раз:")
        return ASK_SAVINGS_5

    context.user_data["setup"]["savings_5"] = int(text)
    await update.message.reply_text("Сколько уходит в накопления 20 числа? (число, или 0 если не уходит)")
    return ASK_SAVINGS_20


async def setup_savings_20(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace(" ", "")
    if not text.isdigit():
        await update.message.reply_text("Нужно число, например 525000. Попробуй ещё раз:")
        return ASK_SAVINGS_20

    context.user_data["setup"]["savings_20"] = int(text)

    data = storage.load_data()
    cycle_start = storage.get_cycle_start()
    cycle = storage.get_or_create_cycle(data, cycle_start)
    old_obligations = cycle.get("obligations", [])

    if old_obligations:
        lines = "\n".join(f"• {o['name']} — {fmt(o['amount'])} ₸" for o in old_obligations)
        await update.message.reply_text(
            f"Твои обязательные платежи в прошлом цикле:\n\n{lines}\n\n"
            f"Они изменились?",
            reply_markup=ReplyKeyboardMarkup(
                [["Нет, всё так же"], ["Да, изменились"]],
                one_time_keyboard=True, resize_keyboard=True
            )
        )
        return ASK_OBLIGATIONS_CHANGED
    else:
        await update.message.reply_text(
            "Перечисли обязательные платежи. Каждый с новой строки в формате:\n"
            "Название Сумма\n\n"
            "Например:\n"
            "Квартира 250000\n"
            "Рассрочка 100000\n"
            "Спорт 30000\n\n"
            "Когда закончишь — отправь всё одним сообщением.",
            reply_markup=ReplyKeyboardRemove()
        )
        return ASK_OBLIGATIONS_LIST


async def setup_obligations_changed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.message.text.strip().lower()

    if "нет" in answer:
        # Keep old obligations as-is, finish setup
        return await finish_setup(update, context, keep_old_obligations=True)
    else:
        await update.message.reply_text(
            "Перечисли обязательные платежи заново. Каждый с новой строки в формате:\n"
            "Название Сумма\n\n"
            "Например:\n"
            "Квартира 250000\n"
            "Рассрочка 100000\n"
            "Спорт 30000\n\n"
            "Когда закончишь — отправь всё одним сообщением.",
            reply_markup=ReplyKeyboardRemove()
        )
        return ASK_OBLIGATIONS_LIST


async def setup_obligations_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    obligations = []
    for line in lines:
        match = re.match(r"^(.+?)\s+(\d[\d\s]*)$", line)
        if match:
            name = match.group(1).strip()
            amount = int(match.group(2).replace(" ", ""))
            obligations.append({"name": name, "amount": amount})

    if not obligations:
        await update.message.reply_text(
            "Не получилось распознать. Формат: Название Сумма, каждый платёж с новой строки. Попробуй ещё раз:"
        )
        return ASK_OBLIGATIONS_LIST

    context.user_data["setup"]["obligations"] = obligations
    return await finish_setup(update, context, keep_old_obligations=False)


async def finish_setup(update: Update, context: ContextTypes.DEFAULT_TYPE, keep_old_obligations: bool):
    data = storage.load_data()
    cycle_start = storage.get_cycle_start()
    cycle = storage.get_or_create_cycle(data, cycle_start)

    setup = context.user_data.get("setup", {})
    cycle["income"] = setup.get("income", cycle.get("income"))
    cycle["savings_5"] = setup.get("savings_5", cycle.get("savings_5"))
    cycle["savings_20"] = setup.get("savings_20", cycle.get("savings_20"))

    if not keep_old_obligations:
        cycle["obligations"] = setup.get("obligations", cycle.get("obligations", []))

    cycle["setup_complete"] = True
    storage.save_data(data)

    avail = storage.available_for_life(cycle)
    daily_limit, remaining_budget, remaining_days = storage.calc_daily_limit(cycle, cycle_start)

    obligations_total = storage.total_obligations(cycle)
    savings_total = (cycle["savings_5"] or 0) + (cycle["savings_20"] or 0)

    summary = (
        f"✅ Бюджет настроен!\n\n"
        f"Доход за месяц: {fmt(cycle['income'])} ₸\n"
        f"Автонакопления: {fmt(savings_total)} ₸\n"
        f"Обязательные расходы: {fmt(obligations_total)} ₸\n"
        f"Доступно на жизнь: {fmt(avail)} ₸\n\n"
        f"Дней в цикле: {storage.days_in_cycle(cycle_start)}\n"
        f"Лимит на день: {fmt(daily_limit)} ₸"
    )

    await update.message.reply_text(summary, reply_markup=main_menu_keyboard())
    context.user_data.pop("setup", None)
    return ConversationHandler.END


async def setup_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("setup", None)
    await update.message.reply_text("Настройка отменена.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ---------- Expense tracking (regular messages) ----------

EXPENSE_PATTERN = re.compile(r"^(.+?)\s+(\d[\d\s]*)$")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    match = EXPENSE_PATTERN.match(text)

    if not match:
        await update.message.reply_text(
            "Не понял 🤔 Напиши в формате: название сумма\nНапример: кофе 2500"
        )
        return

    note = match.group(1).strip()
    amount_str = match.group(2).replace(" ", "")
    amount = int(amount_str)

    if amount <= 0:
        await update.message.reply_text("Сумма должна быть больше нуля 🤔")
        return

    data = storage.load_data()
    cycle_start = storage.get_cycle_start()
    cycle = storage.get_or_create_cycle(data, cycle_start)

    if not cycle.get("setup_complete"):
        await update.message.reply_text(
            "Сначала настрой бюджет командой /setup, чтобы я мог считать лимиты 🙂"
        )
        return

    category = storage.guess_category(note)
    cycle = storage.add_expense(data, amount, category, note)

    today_str = date.today().isoformat()
    spent_today = storage.spent_today(cycle, today_str)

    daily_limit, remaining_budget, remaining_days = storage.calc_daily_limit(cycle, cycle_start)
    remaining_today = daily_limit - spent_today  # roughly: today's limit minus what's spent today already accounted in remaining_budget calc

    # Пересчитываем лимит на сегодня корректно:
    # remaining_budget уже учитывает все траты, включая сегодняшнюю.
    # daily_limit — это новый лимит на оставшиеся дни (включая сегодня).
    remaining_today_after = daily_limit - spent_today

    reply = (
        f"Записал: {category} — {fmt(amount)} ₸\n\n"
        f"Сегодня потрачено: {fmt(spent_today)} ₸\n"
        f"Остаток на сегодня: {fmt(max(remaining_today_after, 0))} ₸\n\n"
        f"До конца цикла осталось: {remaining_days} дн.\n"
        f"Новый дневной лимит: {fmt(daily_limit)} ₸"
    )

    await update.message.reply_text(reply)


# ---------- /status quick command ----------

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = storage.load_data()
    cycle_start = storage.get_cycle_start()
    cycle = storage.get_or_create_cycle(data, cycle_start)

    if not cycle.get("setup_complete"):
        await update.message.reply_text("Бюджет ещё не настроен. Напиши /setup 🙂")
        return

    avail = storage.available_for_life(cycle)
    spent_total = storage.spent_so_far(cycle)
    daily_limit, remaining_budget, remaining_days = storage.calc_daily_limit(cycle, cycle_start)

    reply = (
        f"📊 Текущий бюджет\n\n"
        f"Доступно на жизнь: {fmt(avail)} ₸\n"
        f"Потрачено: {fmt(spent_total)} ₸\n"
        f"Остаток: {fmt(max(remaining_budget, 0))} ₸\n\n"
        f"Осталось дней: {remaining_days}\n"
        f"Дневной лимит: {fmt(daily_limit)} ₸"
    )
    await update.message.reply_text(reply, reply_markup=main_menu_keyboard())


def build_snapshot():
    """Собирает данные текущего цикла в формат для Mini App."""
    data = storage.load_data()
    cycle_start = storage.get_cycle_start()
    cycle = storage.get_or_create_cycle(data, cycle_start)
    cycle_end = storage.get_cycle_end(cycle_start)

    if not cycle.get("setup_complete"):
        return None

    avail = storage.available_for_life(cycle)
    daily_limit, remaining_budget, remaining_days = storage.calc_daily_limit(cycle, cycle_start)
    today_str = date.today().isoformat()
    spent_today_val = storage.spent_today(cycle, today_str)

    days_total = storage.days_in_cycle(cycle_start)
    days_passed = days_total - remaining_days

    # Категории
    categories = {}
    recent_expenses = []
    for day_str, day_data in cycle.get("days_by_date", {}).items():
        for exp in day_data.get("expenses", []):
            categories[exp["category"]] = categories.get(exp["category"], 0) + exp["amount"]
            recent_expenses.append({
                "note": exp["note"],
                "category": exp["category"],
                "amount": exp["amount"],
                "date": day_str,
                "timestamp": exp["timestamp"],
            })

    recent_expenses.sort(key=lambda e: e["timestamp"], reverse=True)

    return {
        "income": cycle["income"] or 0,
        "savings": (cycle["savings_5"] or 0) + (cycle["savings_20"] or 0),
        "obligations": storage.total_obligations(cycle),
        "available": avail,
        "daily_limit": daily_limit,
        "spent_today": spent_today_val,
        "remaining_days": remaining_days,
        "days_total": days_total,
        "days_passed": days_passed,
        "cycle_start": cycle_start.strftime("%d.%m"),
        "cycle_end": cycle_end.strftime("%d.%m"),
        "categories": categories,
        "recent_expenses": recent_expenses,
    }


flask_app = Flask(__name__)
CORS(flask_app)


@flask_app.route("/api/budget", methods=["GET"])
def api_budget():
    snapshot = build_snapshot()
    return jsonify(snapshot)


def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    setup_conv = ConversationHandler(
        entry_points=[CommandHandler("setup", setup_start)],
        states={
            ASK_INCOME: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_income)],
            ASK_SAVINGS_5: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_savings_5)],
            ASK_SAVINGS_20: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_savings_20)],
            ASK_OBLIGATIONS_CHANGED: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_obligations_changed)],
            ASK_OBLIGATIONS_LIST: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_obligations_list)],
        },
        fallbacks=[CommandHandler("cancel", setup_cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(setup_conv)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Бюджет-бот запущен! Нажми Ctrl+C чтобы остановить.")
    app.run_polling()
