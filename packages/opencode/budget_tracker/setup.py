#!/usr/bin/env python3
import sqlite3
import os

def init_database():
    conn = sqlite3.connect("my_budget_tracker.db")
    cursor = conn.cursor()
    
    with open("schema.sql", "r") as f:
        schema = f.read()
    
    cursor.executescript(schema)
    conn.commit()
    conn.close()
    
    print("Database initialized: my_budget_tracker.db")

if __name__ == "__main__":
    init_database()