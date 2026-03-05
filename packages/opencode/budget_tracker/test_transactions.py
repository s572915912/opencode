#!/usr/bin/env python3
import requests
import json
from datetime import datetime, timedelta

BASE_URL = "http://localhost:5000"

class BudgetTrackerTester:
    def __init__(self):
        self.session = requests.Session()
        self.user_id = None
        self.transactions = []
    
    def register(self, username, password):
        print(f"Registering user: {username}")
        data = {"username": username, "password": password}
        response = self.session.post(f"{BASE_URL}/register", json=data)
        print(f"  Status: {response.status_code}")
        if response.status_code == 201:
            print("  ✓ User registered successfully")
        return response
    
    def login(self, username, password):
        print(f"Logging in user: {username}")
        data = {"username": username, "password": password}
        response = self.session.post(f"{BASE_URL}/login", json=data)
        print(f"  Status: {response.status_code}")
        if response.status_code == 200:
            self.user_id = response.json().get("user_id")
            print(f"  ✓ Logged in successfully (User ID: {self.user_id})")
        return response
    
    def add_transaction(self, amount, category, type="expense", description=""):
        print(f"Adding {type}: ${amount} in {category}")
        data = {
            "amount": amount,
            "category": category,
            "type": type,
            "description": description,
            "date": datetime.now().isoformat()
        }
        response = self.session.post(f"{BASE_URL}/transactions", json=data)
        print(f"  Status: {response.status_code}")
        if response.status_code == 201:
            transaction_id = response.json().get("id")
            self.transactions.append(transaction_id)
            print(f"  ✓ Transaction added (ID: {transaction_id})")
        return response
    
    def get_transactions(self, filters=None):
        print("Getting transactions...")
        params = filters or {}
        response = self.session.get(f"{BASE_URL}/transactions", params=params)
        print(f"  Status: {response.status_code}")
        if response.status_code == 200:
            transactions = response.json()
            print(f"  ✓ Found {len(transactions)} transactions")
            return transactions
        return []
    
    def get_summary(self):
        print("Getting summary...")
        response = self.session.get(f"{BASE_URL}/summary")
        print(f"  Status: {response.status_code}")
        if response.status_code == 200:
            summary = response.json()
            print(f"  ✓ Summary retrieved")
            return summary
        return {}
    
    def delete_transaction(self, transaction_id):
        print(f"Deleting transaction {transaction_id}...")
        response = self.session.delete(f"{BASE_URL}/transactions/{transaction_id}")
        print(f"  Status: {response.status_code}")
        if response.status_code == 200:
            print(f"  ✓ Transaction deleted")
        return response
    
    def logout(self):
        print("Logging out...")
        response = self.session.post(f"{BASE_URL}/logout")
        print(f"  Status: {response.status_code}")
        if response.status_code == 200:
            print("  ✓ Logged out successfully")
        return response

def run_comprehensive_test():
    print("=" * 60)
    print("Budget Tracker - Comprehensive Transaction Test")
    print("=" * 60)
    
    tester = BudgetTrackerTester()
    
    # Test 1: Register and login
    print("\n1. User Registration & Login")
    print("-" * 40)
    tester.register("transaction_test", "testpass123")
    tester.login("transaction_test", "testpass123")
    
    # Test 2: Add various transactions
    print("\n2. Adding Transactions")
    print("-" * 40)
    
    # Add expenses
    tester.add_transaction(50.00, "Groceries", "expense", "Weekly grocery shopping")
    tester.add_transaction(25.00, "Transportation", "expense", "Bus fare")
    tester.add_transaction(100.00, "Entertainment", "expense", "Movie tickets")
    tester.add_transaction(75.00, "Dining", "expense", "Restaurant dinner")
    
    # Add income
    tester.add_transaction(2000.00, "Salary", "income", "Monthly salary")
    tester.add_transaction(150.00, "Freelance", "income", "Web design project")
    tester.add_transaction(50.00, "Gift", "income", "Birthday gift")
    
    # Test 3: Get all transactions
    print("\n3. Retrieving All Transactions")
    print("-" * 40)
    all_transactions = tester.get_transactions()
    
    # Test 4: Get filtered transactions
    print("\n4. Filtered Transactions")
    print("-" * 40)
    
    # Get only expenses
    expenses = tester.get_transactions({"type": "expense"})
    print(f"  Found {len(expenses)} expense transactions")
    
    # Get only income
    income = tester.get_transactions({"type": "income"})
    print(f"  Found {len(income)} income transactions")
    
    # Get by category
    groceries = tester.get_transactions({"category": "Groceries"})
    print(f"  Found {len(groceries)} transactions in Groceries category")
    
    # Test 5: Get summary
    print("\n5. Financial Summary")
    print("-" * 40)
    summary = tester.get_summary()
    
    if summary:
        print(f"  Balance: ${summary.get('balance', 0):.2f}")
        print(f"  Total Income: ${summary.get('total_income', 0):.2f}")
        print(f"  Total Expenses: ${summary.get('total_expenses', 0):.2f}")
        
        print("\n  Expenses by Category:")
        for cat in summary.get("expenses_by_category", []):
            print(f"    {cat['category']}: ${cat['total']:.2f}")
        
        print("\n  Income by Category:")
        for cat in summary.get("income_by_category", []):
            print(f"    {cat['category']}: ${cat['total']:.2f}")
        
        print("\n  Recent Transactions:")
        for tx in summary.get("recent_transactions", [])[:3]:
            print(f"    {tx['type']}: ${tx['amount']:.2f} - {tx['category']}")
    
    # Test 6: Delete a transaction
    print("\n6. Transaction Deletion")
    print("-" * 40)
    if tester.transactions:
        transaction_to_delete = tester.transactions[0]
        tester.delete_transaction(transaction_to_delete)
        
        # Verify deletion
        remaining = tester.get_transactions()
        print(f"  Transactions after deletion: {len(remaining)}")
    
    # Test 7: Logout
    print("\n7. Logout")
    print("-" * 40)
    tester.logout()
    
    # Test 8: Try to access protected endpoint after logout
    print("\n8. Access After Logout")
    print("-" * 40)
    response = tester.session.get(f"{BASE_URL}/transactions")
    print(f"  Status: {response.status_code}")
    if response.status_code == 401:
        print("  ✓ Correctly blocked access after logout")
    
    print("\n" + "=" * 60)
    print("All tests completed!")
    print("=" * 60)

if __name__ == "__main__":
    print("Make sure the Flask app is running on port 5000")
    print("Run: python app_fixed.py")
    print()
    run_comprehensive_test()