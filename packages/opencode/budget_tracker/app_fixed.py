from flask import Flask, request, jsonify, session, g, render_template
import sqlite3
from datetime import datetime
import os
from functools import wraps
import hashlib
import secrets
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend
import matplotlib.pyplot as plt
import io
import base64

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key")
app.config["DATABASE"] = "my_budget_tracker.db"

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
    return g.db

def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_db():
    db = get_db()
    
    with app.open_resource("schema.sql") as f:
        db.executescript(f.read().decode("utf8"))

@app.cli.command("init-db")
def init_db_command():
    init_db()
    print("Database initialized.")

app.teardown_appcontext(close_db)

def hash_password(password):
    salt = secrets.token_hex(16)
    salted = salt + password
    hashed = hashlib.sha256(salted.encode()).hexdigest()
    return f"{salt}${hashed}"

def check_password(stored, password):
    if "$" not in stored:
        return False
    salt, hashed = stored.split("$", 1)
    test = hashlib.sha256((salt + password).encode()).hexdigest()
    return test == hashed

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "Login required"}), 401
        return f(*args, **kwargs)
    return decorated

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api")
def api_info():
    return jsonify({"message": "Budget Tracker API", "version": "1.0"})

@app.route("/register", methods=["POST"])
def register():
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON required"}), 400
    
    username = data.get("username")
    password = data.get("password")
    
    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400
    
    db = get_db()
    
    hashed = hash_password(password)
    
    try:
        db.execute(
            "INSERT INTO users (username, password) VALUES (?, ?)",
            (username, hashed)
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "Username already exists"}), 400
    
    return jsonify({"message": "User created"}), 201

@app.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON required"}), 400
    
    username = data.get("username")
    password = data.get("password")
    
    db = get_db()
    user = db.execute(
        "SELECT * FROM users WHERE username = ?", (username,)
    ).fetchone()
    
    if not user or not check_password(user["password"], password):
        return jsonify({"error": "Invalid credentials"}), 401
    
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    
    return jsonify({"message": "Logged in", "user_id": user["id"]})

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"message": "Logged out"})

@app.route("/transactions", methods=["GET", "POST"])
@login_required
def transactions():
    db = get_db()
    
    if request.method == "GET":
        # Get query parameters
        type_filter = request.args.get("type")
        category_filter = request.args.get("category")
        month_filter = request.args.get("month")
        
        query = "SELECT * FROM expenses WHERE user_id = ?"
        params = [session["user_id"]]
        
        if type_filter:
            query += " AND type = ?"
            params.append(type_filter)
        
        if category_filter:
            query += " AND category = ?"
            params.append(category_filter)
        
        if month_filter:
            query += " AND strftime('%Y-%m', date) = ?"
            params.append(month_filter)
        
        query += " ORDER BY date DESC"
        
        transactions = db.execute(query, tuple(params)).fetchall()
        
        return jsonify([dict(t) for t in transactions])
    
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON required"}), 400
    
    amount = data.get("amount")
    category = data.get("category")
    description = data.get("description", "")
    type = data.get("type", "expense")
    date = data.get("date", datetime.now().isoformat())
    
    if not amount or not category:
        return jsonify({"error": "Amount and category required"}), 400
    
    if type not in ["income", "expense"]:
        return jsonify({"error": "Type must be 'income' or 'expense'"}), 400
    
    try:
        cursor = db.execute(
            "INSERT INTO expenses (user_id, amount, category, description, type, date) VALUES (?, ?, ?, ?, ?, ?)",
            (session["user_id"], amount, category, description, type, date)
        )
        db.commit()
        transaction_id = cursor.lastrowid
    except sqlite3.Error as e:
        return jsonify({"error": str(e)}), 500
    
    return jsonify({"message": "Transaction added", "id": transaction_id}), 201

@app.route("/transactions/<int:id>", methods=["DELETE"])
@login_required
def delete_transaction(id):
    db = get_db()
    
    transaction = db.execute(
        "SELECT * FROM expenses WHERE id = ? AND user_id = ?",
        (id, session["user_id"])
    ).fetchone()
    
    if not transaction:
        return jsonify({"error": "Transaction not found"}), 404
    
    db.execute("DELETE FROM expenses WHERE id = ?", (id,))
    db.commit()
    
    return jsonify({"message": "Transaction deleted"})

@app.route("/summary")
@login_required
def summary():
    db = get_db()
    
    # Get total income and expenses
    income_result = db.execute(
        "SELECT SUM(amount) as total FROM expenses WHERE user_id = ? AND type = 'income'",
        (session["user_id"],)
    ).fetchone()
    total_income = income_result["total"] or 0
    
    expense_result = db.execute(
        "SELECT SUM(amount) as total FROM expenses WHERE user_id = ? AND type = 'expense'",
        (session["user_id"],)
    ).fetchone()
    total_expenses = expense_result["total"] or 0
    
    balance = total_income - total_expenses
    
    # Get expenses by category
    expenses_by_category = db.execute(
        "SELECT category, SUM(amount) as total FROM expenses WHERE user_id = ? AND type = 'expense' GROUP BY category ORDER BY total DESC",
        (session["user_id"],)
    ).fetchall()
    
    # Get income by category
    income_by_category = db.execute(
        "SELECT category, SUM(amount) as total FROM expenses WHERE user_id = ? AND type = 'income' GROUP BY category ORDER BY total DESC",
        (session["user_id"],)
    ).fetchall()
    
    # Get monthly summary
    monthly_summary = db.execute(
        "SELECT strftime('%Y-%m', date) as month, type, SUM(amount) as total FROM expenses WHERE user_id = ? GROUP BY month, type ORDER BY month DESC",
        (session["user_id"],)
    ).fetchall()
    
    # Get recent transactions
    recent = db.execute(
        "SELECT * FROM expenses WHERE user_id = ? ORDER BY date DESC LIMIT 5",
        (session["user_id"],)
    ).fetchall()
    
    return jsonify({
        "balance": balance,
        "total_income": total_income,
        "total_expenses": total_expenses,
        "expenses_by_category": [dict(r) for r in expenses_by_category],
        "income_by_category": [dict(r) for r in income_by_category],
        "monthly_summary": [dict(r) for r in monthly_summary],
        "recent_transactions": [dict(r) for r in recent]
    })

@app.route("/charts/expense-categories")
@login_required
def expense_categories_chart():
    db = get_db()
    
    expenses_by_category = db.execute(
        "SELECT category, SUM(amount) as total FROM expenses WHERE user_id = ? AND type = 'expense' GROUP BY category ORDER BY total DESC",
        (session["user_id"],)
    ).fetchall()
    
    if not expenses_by_category:
        return jsonify({"error": "No expense data available"}), 404
    
    categories = [row["category"] for row in expenses_by_category]
    amounts = [row["total"] for row in expenses_by_category]
    
    # Create pie chart
    plt.figure(figsize=(8, 6))
    plt.pie(amounts, labels=categories, autopct="%1.1f%%", startangle=90)
    plt.title("Expense Distribution by Category")
    plt.axis("equal")  # Equal aspect ratio ensures pie is drawn as a circle
    
    # Save to buffer
    buf = io.BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight")
    plt.close()
    buf.seek(0)
    
    # Convert to base64
    img_base64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    
    return jsonify({
        "chart_type": "pie",
        "image": f"data:image/png;base64,{img_base64}",
        "data": [dict(r) for r in expenses_by_category]
    })

@app.route("/charts/monthly-trend")
@login_required
def monthly_trend_chart():
    db = get_db()
    
    monthly_data = db.execute(
        "SELECT strftime('%Y-%m', date) as month, type, SUM(amount) as total FROM expenses WHERE user_id = ? GROUP BY month, type ORDER BY month",
        (session["user_id"],)
    ).fetchall()
    
    if not monthly_data:
        return jsonify({"error": "No monthly data available"}), 404
    
    # Organize data by month
    months = sorted(set(row["month"] for row in monthly_data))
    income_by_month = {row["month"]: row["total"] for row in monthly_data if row["type"] == "income"}
    expense_by_month = {row["month"]: row["total"] for row in monthly_data if row["type"] == "expense"}
    
    income_values = [income_by_month.get(month, 0) for month in months]
    expense_values = [expense_by_month.get(month, 0) for month in months]
    
    # Create bar chart
    plt.figure(figsize=(10, 6))
    x = range(len(months))
    width = 0.35
    
    plt.bar([i - width/2 for i in x], income_values, width, label="Income", color="green")
    plt.bar([i + width/2 for i in x], expense_values, width, label="Expenses", color="red")
    
    plt.xlabel("Month")
    plt.ylabel("Amount ($)")
    plt.title("Monthly Income vs Expenses")
    plt.xticks(x, months, rotation=45)
    plt.legend()
    plt.tight_layout()
    
    # Save to buffer
    buf = io.BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight")
    plt.close()
    buf.seek(0)
    
    # Convert to base64
    img_base64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    
    return jsonify({
        "chart_type": "bar",
        "image": f"data:image/png;base64,{img_base64}",
        "months": months,
        "income": income_values,
        "expenses": expense_values
    })

@app.route("/charts/balance-overview")
@login_required
def balance_overview_chart():
    db = get_db()
    
    # Get data for the chart
    summary_data = db.execute(
        "SELECT strftime('%Y-%m', date) as month, type, SUM(amount) as total FROM expenses WHERE user_id = ? GROUP BY month, type ORDER BY month",
        (session["user_id"],)
    ).fetchall()
    
    if not summary_data:
        return jsonify({"error": "No data available"}), 404
    
    # Calculate balance per month
    months = sorted(set(row["month"] for row in summary_data))
    monthly_balance = []
    
    for month in months:
        income = sum(row["total"] for row in summary_data if row["month"] == month and row["type"] == "income")
        expense = sum(row["total"] for row in summary_data if row["month"] == month and row["type"] == "expense")
        monthly_balance.append(income - expense)
    
    # Create line chart
    plt.figure(figsize=(10, 6))
    plt.plot(months, monthly_balance, marker="o", linestyle="-", color="blue", linewidth=2)
    plt.axhline(y=0, color="gray", linestyle="--", alpha=0.5)
    
    plt.xlabel("Month")
    plt.ylabel("Balance ($)")
    plt.title("Monthly Balance Trend")
    plt.xticks(rotation=45)
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    
    # Save to buffer
    buf = io.BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight")
    plt.close()
    buf.seek(0)
    
    # Convert to base64
    img_base64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    
    return jsonify({
        "chart_type": "line",
        "image": f"data:image/png;base64,{img_base64}",
        "months": months,
        "balance": monthly_balance
    })

if __name__ == "__main__":
    app.run(port=5000, debug=True)