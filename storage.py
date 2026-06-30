import json
import os
from datetime import datetime, date

DATA_FILE = os.path.join(os.path.dirname(__file__), "budget_data.json")

DEFAULT_DATA = {
    "cycles": {},      # key: "2026-06-10" (cycle start date) -> cycle data
    "current_cycle": None,  # cycle start date string, e.g. "2026-06-10"
}

CATEGORY_KEYWORDS = {
    "Еда": ["кофе", "продукты", "доставка", "кафе", "ресторан", "обед", "завтрак", "ужин", "магазин", "супермаркет", "еда", "перекус"],
    "Транспорт": ["такси", "бензин", "заправка", "метро", "автобус", "парковка", "транспорт"],
    "Дом": ["бытовые", "хозтовары", "дом", "ремонт", "мебель", "посуда"],
    "Семья": ["ребенок", "ребёнок", "семья", "детям", "садик", "школа", "игрушки"],
    "Личное": ["одежда", "техника", "обувь", "косметика", "стрижка", "подарок", "гаджет"],
}


def _today_data():
    return {
        "expenses": [],   # list of {amount, category, note, timestamp}
    }


def load_data():
    if not os.path.exists(DATA_FILE):
        return json.loads(json.dumps(DEFAULT_DATA))
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def guess_category(text):
    text_lower = text.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                return category
    return "Незапланированное"


def get_cycle_start(d=None):
    """Returns the start date (10th) of the current budget cycle as a date object."""
    if d is None:
        d = date.today()
    if d.day >= 10:
        return date(d.year, d.month, 10)
    else:
        month = d.month - 1
        year = d.year
        if month == 0:
            month = 12
            year -= 1
        return date(year, month, 10)


def get_cycle_end(cycle_start):
    """Returns the last day of the cycle (9th of next month)."""
    month = cycle_start.month + 1
    year = cycle_start.year
    if month == 13:
        month = 1
        year += 1
    return date(year, month, 9)


def cycle_key(cycle_start):
    return cycle_start.isoformat()


def get_or_create_cycle(data, cycle_start=None):
    if cycle_start is None:
        cycle_start = get_cycle_start()
    key = cycle_key(cycle_start)

    if key not in data["cycles"]:
        # Try to carry over obligations from previous cycle
        prev_obligations = []
        if data["cycles"]:
            last_key = sorted(data["cycles"].keys())[-1]
            prev_obligations = data["cycles"][last_key].get("obligations", [])

        data["cycles"][key] = {
            "start": key,
            "income": None,
            "savings_5": None,
            "savings_20": None,
            "obligations": prev_obligations,  # list of {name, amount}
            "days_by_date": {},  # "2026-06-15" -> {"expenses": [...]}
            "setup_complete": False,
        }
        data["current_cycle"] = key
        save_data(data)

    return data["cycles"][key]


def total_obligations(cycle):
    return sum(o["amount"] for o in cycle.get("obligations", []))


def available_for_life(cycle):
    income = cycle.get("income") or 0
    savings = (cycle.get("savings_5") or 0) + (cycle.get("savings_20") or 0)
    obligations = total_obligations(cycle)
    return income - savings - obligations


def days_in_cycle(cycle_start):
    cycle_end = get_cycle_end(cycle_start)
    return (cycle_end - cycle_start).days + 1


def days_remaining(cycle_start, today=None):
    if today is None:
        today = date.today()
    cycle_end = get_cycle_end(cycle_start)
    remaining = (cycle_end - today).days + 1
    return max(remaining, 0)


def spent_so_far(cycle):
    total = 0
    for day_data in cycle.get("days_by_date", {}).values():
        for exp in day_data.get("expenses", []):
            total += exp["amount"]
    return total


def spent_today(cycle, today_str):
    day_data = cycle.get("days_by_date", {}).get(today_str, {"expenses": []})
    return sum(e["amount"] for e in day_data["expenses"])


def add_expense(data, amount, category, note):
    cycle_start = get_cycle_start()
    cycle = get_or_create_cycle(data, cycle_start)

    today_str = date.today().isoformat()
    if today_str not in cycle["days_by_date"]:
        cycle["days_by_date"][today_str] = {"expenses": []}

    cycle["days_by_date"][today_str]["expenses"].append({
        "amount": amount,
        "category": category,
        "note": note,
        "timestamp": datetime.now().isoformat(),
    })

    save_data(data)
    return cycle


def calc_daily_limit(cycle, cycle_start, today=None):
    """Recalculates the daily limit based on remaining budget and remaining days."""
    if today is None:
        today = date.today()

    avail = available_for_life(cycle)
    spent_total = spent_so_far(cycle)
    remaining_budget = avail - spent_total

    remaining_days = days_remaining(cycle_start, today)
    if remaining_days <= 0:
        remaining_days = 1

    daily_limit = remaining_budget / remaining_days
    return daily_limit, remaining_budget, remaining_days
