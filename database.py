import sqlite3

# Database connection details
DATABASE_NAME = 'database.db'

# Connect to the database
def connect():
    connection = sqlite3.connect(DATABASE_NAME)
    return connection

# Admin Management
class Admin:
    def create_admin(self, username, password):
        conn = connect()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO admins (username, password) VALUES (?, ?)', (username, password))
        conn.commit()
        conn.close()

    def get_admin(self, admin_id):
        conn = connect()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM admins WHERE id=?', (admin_id,))
        admin = cursor.fetchone()
        conn.close()
        return admin

# User Management
class User:
    def create_user(self, username, password):
        conn = connect()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO users (username, password) VALUES (?, ?)', (username, password))
        conn.commit()
        conn.close()

    def get_user(self, user_id):
        conn = connect()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE id=?', (user_id,))
        user = cursor.fetchone()
        conn.close()
        return user

# Plans Management
class Plan:
    def create_plan(self, plan_name, price):
        conn = connect()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO plans (plan_name, price) VALUES (?, ?)', (plan_name, price))
        conn.commit()
        conn.close()

    def get_plan(self, plan_id):
        conn = connect()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM plans WHERE id=?', (plan_id,))
        plan = cursor.fetchone()
        conn.close()
        return plan

# Payments Management
class Payment:
    def process_payment(self, user_id, amount):
        conn = connect()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO payments (user_id, amount) VALUES (?, ?)', (user_id, amount))
        conn.commit()
        conn.close()

# Permissions Management
class Permission:
    def grant_permission(self, user_id, permission):
        conn = connect()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO permissions (user_id, permission) VALUES (?, ?)', (user_id, permission))
        conn.commit()
        conn.close()

    def revoke_permission(self, user_id, permission):
        conn = connect()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM permissions WHERE user_id=? AND permission=?', (user_id, permission))
        conn.commit()
        conn.close()

# Main function to create necessary tables
if __name__ == '__main__':
    conn = connect()
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS admins (
                        id INTEGER PRIMARY KEY,
                        username TEXT NOT NULL UNIQUE,
                        password TEXT NOT NULL
                    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY,
                        username TEXT NOT NULL UNIQUE,
                        password TEXT NOT NULL
                    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS plans (
                        id INTEGER PRIMARY KEY,
                        plan_name TEXT NOT NULL,
                        price REAL NOT NULL
                    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS payments (
                        id INTEGER PRIMARY KEY,
                        user_id INTEGER,
                        amount REAL,
                        FOREIGN KEY (user_id) REFERENCES users (id)
                    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS permissions (
                        id INTEGER PRIMARY KEY,
                        user_id INTEGER,
                        permission TEXT,
                        FOREIGN KEY (user_id) REFERENCES users (id)
                    )''')
    conn.commit()
    conn.close()