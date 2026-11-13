import sqlite3
from werkzeug.security import generate_password_hash as _gph, check_password_hash


def generate_password_hash(pw):
    # pbkdf2 works on every Python build (scrypt is missing on macOS system Python 3.9)
    return _gph(pw, method="pbkdf2:sha256")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
  email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN('admin','student')));
CREATE TABLE IF NOT EXISTS students(user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  roll_no TEXT UNIQUE NOT NULL, department TEXT);
CREATE TABLE IF NOT EXISTS books(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, author TEXT NOT NULL,
  isbn TEXT UNIQUE NOT NULL, category TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity>=0),
  available INTEGER NOT NULL CHECK(available>=0));
CREATE TABLE IF NOT EXISTS transactions(id INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id INTEGER NOT NULL REFERENCES books(id), student_id INTEGER NOT NULL REFERENCES users(id),
  issue_date TEXT NOT NULL, due_date TEXT NOT NULL, return_date TEXT, fine REAL DEFAULT 0);
"""

class DatabaseManager:
    """Single point of access to SQLite (encapsulates connection handling)."""
    def __init__(self, path="library.db"):
        self._path = path

    def _connect(self):
        c = sqlite3.connect(self._path)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return c

    def query(self, sql, args=()):
        with self._connect() as c:
            return c.execute(sql, args).fetchall()

    def one(self, sql, args=()):
        r = self.query(sql, args)
        return r[0] if r else None

    def execute(self, sql, args=()):
        with self._connect() as c:
            return c.execute(sql, args).lastrowid

    def initialize(self):
        with self._connect() as c:
            c.executescript(SCHEMA)
        if not self.one("SELECT id FROM users LIMIT 1"):
            self._seed()

    def _seed(self):
        from datetime import date, timedelta
        self.execute("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,'admin')",
                     ("Librarian", "admin@library.com", generate_password_hash("admin123")))
        for n, e, r in [("Ankit Pandey", "ankit@student.com", "25/SET/BCSE/070"), ("Riya Sharma", "riya@student.com", "25/SET/BCSE/071")]:
            uid = self.execute("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,'student')",
                               (n, e, generate_password_hash("student123")))
            self.execute("INSERT INTO students VALUES(?,?,?)", (uid, r, "CSE"))
        books = [("Introduction to Algorithms", "Cormen", "9780262033848", "Computer Science", 4),
                 ("Clean Code", "Robert Martin", "9780132350884", "Computer Science", 3),
                 ("Fluent Python", "Luciano Ramalho", "9781492056355", "Programming", 3),
                 ("Database System Concepts", "Silberschatz", "9780078022159", "Computer Science", 2),
                 ("Wings of Fire", "A.P.J. Abdul Kalam", "9788173711466", "Biography", 5),
                 ("Engineering Mathematics", "B.S. Grewal", "9788174091956", "Mathematics", 6)]
        for t, a, i, c, q in books:
            self.execute("INSERT INTO books(title,author,isbn,category,quantity,available) VALUES(?,?,?,?,?,?)", (t, a, i, c, q, q))
        today = date.today()
        # one overdue, one active
        for bid, sid, ago in [(1, 2, 25), (2, 3, 3)]:
            d = today - timedelta(days=ago)
            self.execute("INSERT INTO transactions(book_id,student_id,issue_date,due_date) VALUES(?,?,?,?)",
                         (bid, sid, d.isoformat(), (d + timedelta(days=14)).isoformat()))
            self.execute("UPDATE books SET available=available-1 WHERE id=?", (bid,))