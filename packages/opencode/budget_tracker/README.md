# Budget Tracker - Flask Application

A simple personal budget tracker web application built with Flask 2.3.1 and SQLite.

## Features

- User registration and authentication
- Expense and income tracking
- Category-based expense organization
- Basic analytics and summaries
- RESTful API endpoints

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Initialize Database

```bash
python setup.py
```

Or using Flask CLI:

```bash
export FLASK_APP=app_fixed.py
flask init-db
```

### 3. Run the Application

```bash
python app_fixed.py
```

The application will run on `http://localhost:5000`

## API Endpoints

### Authentication

- `POST /register` - Register new user
- `POST /login` - Login user
- `POST /logout` - Logout user

### Expenses

- `GET /expenses` - Get all expenses for logged-in user
- `POST /expenses` - Add new expense
- `DELETE /expenses/<id>` - Delete expense

### Analytics

- `GET /summary` - Get expense summary by category

## Database Schema

### Users Table

- `id` - Primary key
- `username` - Unique username
- `password` - User password (plain text in this example - use hashing in production)
- `created_at` - Timestamp

### Expenses Table

- `id` - Primary key
- `user_id` - Foreign key to users table
- `amount` - Transaction amount
- `category` - Expense category
- `description` - Optional description
- `date` - Transaction date
- `type` - 'income' or 'expense'

## Project Structure

```
budget_tracker/
├── app_fixed.py          # Main Flask application
├── app.py               # Original app with SQLAlchemy
├── requirements.txt     # Python dependencies
├── schema.sql          # Database schema
├── setup.py           # Database initialization script
└── README.md          # This file
```

## Improvements Over Original Code

1. **Proper Database Connection Management**: Uses Flask's `g` object for request-scoped connections
2. **Error Handling**: Better error responses and validation
3. **Security**: Session-based authentication with `@login_required` decorator
4. **Database Schema**: Added indexes, foreign keys, and constraints
5. **CLI Support**: Flask CLI command for database initialization
6. **Code Organization**: Separated concerns with proper function structure

## Next Steps

1. Add password hashing with Werkzeug or bcrypt
2. Implement frontend templates (HTML/CSS/JavaScript)
3. Add data visualization with Matplotlib/Chart.js
4. Add unit tests
5. Deploy to production server

## Notes

- This is a development setup. For production:
  - Use environment variables for secrets
  - Implement proper password hashing
  - Add CSRF protection
  - Use a production WSGI server (Gunicorn, uWSGI)
  - Consider using PostgreSQL instead of SQLite for concurrent access
