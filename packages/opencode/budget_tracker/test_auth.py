#!/usr/bin/env python3
import requests
import json

BASE_URL = "http://localhost:5000"

def test_auth():
    print("Testing Budget Tracker Authentication API")
    print("=" * 50)
    
    # Test 1: Register new user
    print("\n1. Testing user registration...")
    reg_data = {
        "username": "testuser",
        "password": "testpass123"
    }
    
    try:
        response = requests.post(f"{BASE_URL}/register", json=reg_data)
        print(f"   Status: {response.status_code}")
        print(f"   Response: {response.json()}")
    except Exception as e:
        print(f"   Error: {e}")
        return
    
    # Test 2: Login with correct credentials
    print("\n2. Testing login with correct credentials...")
    login_data = {
        "username": "testuser",
        "password": "testpass123"
    }
    
    try:
        response = requests.post(f"{BASE_URL}/login", json=login_data)
        print(f"   Status: {response.status_code}")
        print(f"   Response: {response.json()}")
        
        if response.status_code == 200:
            session_cookie = response.cookies.get("session")
            print(f"   Session cookie: {'Set' if session_cookie else 'Not set'}")
    except Exception as e:
        print(f"   Error: {e}")
    
    # Test 3: Login with wrong password
    print("\n3. Testing login with wrong password...")
    wrong_data = {
        "username": "testuser",
        "password": "wrongpass"
    }
    
    try:
        response = requests.post(f"{BASE_URL}/login", json=wrong_data)
        print(f"   Status: {response.status_code}")
        print(f"   Response: {response.json()}")
    except Exception as e:
        print(f"   Error: {e}")
    
    # Test 4: Try to access protected endpoint without login
    print("\n4. Testing protected endpoint without login...")
    try:
        response = requests.get(f"{BASE_URL}/expenses")
        print(f"   Status: {response.status_code}")
        print(f"   Response: {response.json()}")
    except Exception as e:
        print(f"   Error: {e}")
    
    # Test 5: Register duplicate user
    print("\n5. Testing duplicate user registration...")
    try:
        response = requests.post(f"{BASE_URL}/register", json=reg_data)
        print(f"   Status: {response.status_code}")
        print(f"   Response: {response.json()}")
    except Exception as e:
        print(f"   Error: {e}")
    
    print("\n" + "=" * 50)
    print("Authentication tests completed!")

if __name__ == "__main__":
    print("Make sure the Flask app is running on port 5000")
    print("Run: python app_fixed.py")
    print()
    test_auth()