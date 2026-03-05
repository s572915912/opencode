#!/usr/bin/env python3
"""
Budget Tracker - Run Script

This script helps you set up and run the budget tracker application.
"""

import os
import sys
import subprocess
import sqlite3

def check_dependencies():
    """Check if required packages are installed."""
    print("Checking dependencies...")
    
    try:
        import flask
        print(f"  ✓ Flask {flask.__version__}")
    except ImportError:
        print("  ✗ Flask not installed")
        return False
    
    try:
        import matplotlib
        print(f"  ✓ Matplotlib {matplotlib.__version__}")
    except ImportError:
        print("  ✗ Matplotlib not installed")
        return False
    
    return True

def setup_database():
    """Initialize the database."""
    print("\nSetting up database...")
    
    if not os.path.exists("schema.sql"):
        print("  ✗ schema.sql not found")
        return False
    
    try:
        # Remove existing database
        if os.path.exists("my_budget_tracker.db"):
            os.remove("my_budget_tracker.db")
            print("  ✓ Removed existing database")
        
        # Create new database
        conn = sqlite3.connect("my_budget_tracker.db")
        cursor = conn.cursor()
        
        with open("schema.sql", "r") as f:
            schema = f.read()
        
        cursor.executescript(schema)
        conn.commit()
        conn.close()
        
        print("  ✓ Database created: my_budget_tracker.db")
        return True
    except Exception as e:
        print(f"  ✗ Error creating database: {e}")
        return False

def install_dependencies():
    """Install required packages."""
    print("\nInstalling dependencies...")
    
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
        print("  ✓ Dependencies installed")
        return True
    except subprocess.CalledProcessError:
        print("  ✗ Failed to install dependencies")
        return False

def run_application():
    """Run the Flask application."""
    print("\nStarting Budget Tracker...")
    print("=" * 50)
    print("Application will be available at: http://localhost:5000")
    print("Press Ctrl+C to stop the server")
    print("=" * 50)
    
    try:
        # Import and run the app
        from app_fixed import app
        app.run(port=5000, debug=True)
    except KeyboardInterrupt:
        print("\n\nServer stopped by user")
    except Exception as e:
        print(f"\nError starting application: {e}")
        return False
    
    return True

def main():
    """Main entry point."""
    print("=" * 50)
    print("Budget Tracker Setup")
    print("=" * 50)
    
    # Check if we need to install dependencies
    if not check_dependencies():
        print("\nSome dependencies are missing.")
        choice = input("Do you want to install them? (y/n): ")
        if choice.lower() == 'y':
            if not install_dependencies():
                print("Failed to install dependencies. Exiting.")
                return
        else:
            print("Please install dependencies manually:")
            print("  pip install -r requirements.txt")
            return
    
    # Setup database
    if not setup_database():
        print("Failed to setup database. Exiting.")
        return
    
    # Run application
    run_application()

if __name__ == "__main__":
    main()