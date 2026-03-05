from flask import Flask, request, jsonify, session, render_template, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import matplotlib.pyplot as plt
import io
import base64
from functools import wraps
import json

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///budget.db"
app.config["SECRET_KEY"] = "your-secret-key-change-this"
db = SQLAlchemy(app)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    expenses = db.relationship("Expense", backref="user", lazy=True)

class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    amount = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(80), nullable=False)
    description = db.Column(db.String(200))
    type = db.Column(db.String(10), nullable=False)  # 'income' or 'expense'

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "Login required"}), 401
        return f(*args, **kwargs)
    return decorated

@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return render_template("index.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("register.html")
    
    data = request.get_json() if request.is_json else request.form
    username = data.get("username")
    password = data.get("password")
    
    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400
    
    if User.query.filter_by(username=username).first():
        return jsonify({"error": "Username already exists"}), 400
    
    hashed = generate_password_hash(password)
    user = User(username=username, password=hashed)
    db.session.add(user)
    db.session.commit()
    
    return jsonify({"message": "User created"}), 201

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")
    
    data = request.get_json() if request.is_json else request.form
    username = data.get("username")
    password = data.get("password")
    
    user = User.query.filter_by(username=username).first()
    if not user or not check_password_hash(user.password, password):
        return jsonify({"error": "Invalid credentials"}), 401
    
    session["user_id"] = user.id
    session["username"] = user.username
    
    return jsonify({"message": "Logged in", "user_id": user.id})

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

@app.route("/dashboard")
@login_required
def dashboard():
    user = User.query.get(session["user_id"])
    
    expenses = Expense.query.filter_by(user_id=user.id, type="expense").all()
    income = Expense.query.filter_by(user_id=user.id, type="income").all()
    
    total_expenses = sum(e.amount for e in expenses)
    total_income = sum(i.amount for i in income)
    balance = total_income - total_expenses
    
    recent = Expense.query.filter_by(user_id=user.id).order_by(Expense.date.desc()).limit(10).all()
    
    return render_template("dashboard.html", 
                         username=user.username,
                         total_expenses=total_expenses,
                         total_income=total_income,
                         balance=balance,
                         recent=recent)

@app.route("/expenses", methods=["GET", "POST"])
@login_required
def expenses():
    if request.method == "GET":
        user = User.query.get(session["user_id"])
        expenses = Expense.query.filter_by(user_id=user.id).order_by(Expense.date.desc()).all()
        
        if request.headers.get("Accept") == "application/json":
            return jsonify([{
                "id": e.id,
                "date": e.date.isoformat(),
                "amount": e.amount,
                "category": e.category,
                "description": e.description,
                "type": e.type
            } for e in expenses])
        
        return render_template("expenses.html", expenses=expenses)
    
    data = request.get_json() if request.is_json else request.form
    amount = float(data.get("amount", 0))
    category = data.get("category", "")
    description = data.get("description", "")
    type = data.get("type", "expense")
    date_str = data.get("date")
    
    if not amount or not category:
        return jsonify({"error": "Amount and category required"}), 400
    
    date = datetime.fromisoformat(date_str) if date_str else datetime.utcnow()
    
    expense = Expense(
        user_id=session["user_id"],
        amount=amount,
        category=category,
        description=description,
        type=type,
        date=date
    )
    
    db.session.add(expense)
    db.session.commit()
    
    return jsonify({"message": "Transaction added", "id": expense.id}), 201

@app.route("/expenses/<int:id>", methods=["DELETE"])
@login_required
def delete_expense(id):
    expense = Expense.query.get_or_404(id)
    
    if expense.user_id != session["user_id"]:
        return jsonify({"error": "Unauthorized"}), 403
    
    db.session.delete(expense)
    db.session.commit()
    
    return jsonify({"message": "Transaction deleted"})

@app.route("/analytics")
@login_required
def analytics():
    user = User.query.get(session["user_id"])
    
    expenses = Expense.query.filter_by(user_id=user.id, type="expense").all()
    income = Expense.query.filter_by(user_id=user.id, type="income").all()
    
    category_totals = {}
    for e in expenses:
        category_totals[e.category] = category_totals.get(e.category, 0) + e.amount
    
    monthly_data = {}
    for e in expenses:
        month = e.date.strftime("%Y-%m")
        monthly_data[month] = monthly_data.get(month, 0) + e.amount
    
    return render_template("analytics.html",
                         category_totals=category_totals,
                         monthly_data=monthly_data,
                         total_expenses=sum(e.amount for e in expenses),
                         total_income=sum(i.amount for i in income))

@app.route("/chart/categories")
@login_required
def category_chart():
    user = User.query.get(session["user_id"])
    expenses = Expense.query.filter_by(user_id=user.id, type="expense").all()
    
    category_totals = {}
    for e in expenses:
        category_totals[e.category] = category_totals.get(e.category, 0) + e.amount
    
    if not category_totals:
        return jsonify({"error": "No data"}), 404
    
    plt.figure(figsize=(8, 6))
    plt.pie(category_totals.values(), labels=category_totals.keys(), autopct="%1.1f%%")
    plt.title("Spending by Category")
    
    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    plt.close()
    buf.seek(0)
    
    img_base64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return jsonify({"image": f"data:image/png;base64,{img_base64}"})

@app.route("/chart/monthly")
@login_required
def monthly_chart():
    user = User.query.get(session["user_id"])
    expenses = Expense.query.filter_by(user_id=user.id, type="expense").all()
    
    monthly_totals = {}
    for e in expenses:
        month = e.date.strftime("%Y-%m")
        monthly_totals[month] = monthly_totals.get(month, 0) + e.amount
    
    if not monthly_totals:
        return jsonify({"error": "No data"}), 404
    
    months = sorted(monthly_totals.keys())
    amounts = [monthly_totals[m] for m in months]
    
    plt.figure(figsize=(10, 6))
    plt.bar(months, amounts)
    plt.xlabel("Month")
    plt.ylabel("Amount")
    plt.title("Monthly Spending")
    plt.xticks(rotation=45)
    
    buf = io.BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight")
    plt.close()
    buf.seek(0)
    
    img_base64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return jsonify({"image": f"data:image/png;base64,{img_base64}"})

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)