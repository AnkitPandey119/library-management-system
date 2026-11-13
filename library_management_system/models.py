from abc import ABC, abstractmethod
from datetime import date, timedelta
from werkzeug.security import generate_password_hash as _gph, check_password_hash


def generate_password_hash(pw):
    # pbkdf2 works on every Python build (scrypt is missing on macOS system Python 3.9)
    return _gph(pw, method="pbkdf2:sha256")


class LibraryError(Exception):
    """Raised for any business-rule violation."""


class User(ABC):
    """Abstract base class for all users."""
    def __init__(self, uid, name, email, role):
        self.id, self.name, self.email = uid, name, email
        self._role = role  # encapsulated

    @property
    def role(self):
        return self._role

    @abstractmethod
    def permissions(self): ...

    def can(self, action):
        return action in self.permissions()

    @classmethod
    def from_row(cls, row):
        klass = Admin if row["role"] == "admin" else Student
        return klass(row["id"], row["name"], row["email"])


class Admin(User):
    def __init__(self, uid, name, email):
        super().__init__(uid, name, email, "admin")

    def permissions(self):  # polymorphism / overriding
        return {"manage_books", "manage_students", "issue", "return", "reports"}


class Student(User):
    def __init__(self, uid, name, email):
        super().__init__(uid, name, email, "student")

    def permissions(self):
        return {"borrow", "view_own"}


class Book:
    def __init__(self, title, author, isbn, category, quantity, available=None, bid=None):
        if not title.strip() or not author.strip() or not isbn.strip():
            raise LibraryError("Title, author and ISBN are required.")
        if int(quantity) < 0:
            raise LibraryError("Quantity cannot be negative.")
        self.id, self.title, self.author = bid, title.strip(), author.strip()
        self.isbn, self.category = isbn.strip(), category.strip() or "General"
        self.quantity = int(quantity)
        self._available = self.quantity if available is None else int(available)

    @property
    def available(self):
        return self._available

    def is_available(self):
        return self._available > 0


class Transaction:
    LOAN_DAYS = 14
    FINE_PER_DAY = 5  # Rs.

    @classmethod
    def due_date_from(cls, issued):
        return issued + timedelta(days=cls.LOAN_DAYS)

    @classmethod
    def fine_for(cls, due_iso, returned=None):
        returned = returned or date.today()
        late = (returned - date.fromisoformat(due_iso)).days
        return max(0, late) * cls.FINE_PER_DAY


class Library:
    """Business logic; all persistence goes through DatabaseManager."""
    def __init__(self, db):
        self.db = db

    # --- auth
    def authenticate(self, email, password):
        r = self.db.one("SELECT * FROM users WHERE email=?", (email.strip().lower(),))
        if r and check_password_hash(r["password_hash"], password):
            return User.from_row(r)
        return None

    def register_student(self, name, email, password, roll_no, dept):
        if len(password) < 6:
            raise LibraryError("Password must be at least 6 characters.")
        if not name.strip() or "@" not in email:
            raise LibraryError("Enter a valid name and email.")
        try:
            uid = self.db.execute("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,'student')",
                                  (name.strip(), email.strip().lower(), generate_password_hash(password)))
            self.db.execute("INSERT INTO students VALUES(?,?,?)", (uid, roll_no.strip(), dept.strip()))
        except Exception:
            self.db.execute("DELETE FROM users WHERE email=? AND id NOT IN (SELECT user_id FROM students)", (email.strip().lower(),))
            raise LibraryError("Email or roll number already registered.")

    # --- books
    def save_book(self, book):
        try:
            if book.id:
                used = book.quantity - self.db.one("SELECT available FROM books WHERE id=?", (book.id,))["available"]
                cur = self.db.one("SELECT quantity,available FROM books WHERE id=?", (book.id,))
                out = cur["quantity"] - cur["available"]
                if book.quantity < out:
                    raise LibraryError("Quantity is less than copies currently issued.")
                self.db.execute("UPDATE books SET title=?,author=?,isbn=?,category=?,quantity=?,available=? WHERE id=?",
                                (book.title, book.author, book.isbn, book.category, book.quantity, book.quantity - out, book.id))
            else:
                self.db.execute("INSERT INTO books(title,author,isbn,category,quantity,available) VALUES(?,?,?,?,?,?)",
                                (book.title, book.author, book.isbn, book.category, book.quantity, book.available))
        except LibraryError:
            raise
        except Exception:
            raise LibraryError("A book with this ISBN already exists.")

    def delete_book(self, bid):
        if self.db.one("SELECT id FROM transactions WHERE book_id=? AND return_date IS NULL", (bid,)):
            raise LibraryError("Cannot delete a book that is currently issued.")
        self.db.execute("DELETE FROM transactions WHERE book_id=?", (bid,))
        self.db.execute("DELETE FROM books WHERE id=?", (bid,))

    def search_books(self, q="", category=""):
        like = f"%{q}%"
        return self.db.query("SELECT * FROM books WHERE (title LIKE ? OR author LIKE ? OR isbn LIKE ?) AND (?='' OR category=?) ORDER BY title",
                             (like, like, like, category, category))

    def categories(self):
        return [r[0] for r in self.db.query("SELECT DISTINCT category FROM books ORDER BY category")]

    # --- transactions
    def issue_book(self, book_id, student_id):
        b = self.db.one("SELECT * FROM books WHERE id=?", (book_id,))
        s = self.db.one("SELECT id FROM users WHERE id=? AND role='student'", (student_id,))
        if not b or not s:
            raise LibraryError("Invalid book or student.")
        if b["available"] < 1:
            raise LibraryError("No copies of this book are available.")
        if self.db.one("SELECT id FROM transactions WHERE book_id=? AND student_id=? AND return_date IS NULL", (book_id, student_id)):
            raise LibraryError("This student already has this book.")
        if self.db.one("SELECT COUNT(*) c FROM transactions WHERE student_id=? AND return_date IS NULL", (student_id,))["c"] >= 3:
            raise LibraryError("Borrow limit of 3 books reached.")
        today = date.today()
        self.db.execute("INSERT INTO transactions(book_id,student_id,issue_date,due_date) VALUES(?,?,?,?)",
                        (book_id, student_id, today.isoformat(), Transaction.due_date_from(today).isoformat()))
        self.db.execute("UPDATE books SET available=available-1 WHERE id=?", (book_id,))

    def return_book(self, tid, student_id=None):
        t = self.db.one("SELECT * FROM transactions WHERE id=?", (tid,))
        if not t or t["return_date"]:
            raise LibraryError("Invalid or already returned transaction.")
        fine = Transaction.fine_for(t["due_date"])
        self.db.execute("UPDATE transactions SET return_date=?,fine=? WHERE id=?", (date.today().isoformat(), fine, tid))
        self.db.execute("UPDATE books SET available=available+1 WHERE id=?", (t["book_id"],))
        return fine

    def transactions(self, flt="all", student_id=None):
        sql = """SELECT t.*, b.title, u.name student, s.roll_no FROM transactions t JOIN books b ON b.id=t.book_id
                 JOIN users u ON u.id=t.student_id JOIN students s ON s.user_id=u.id WHERE 1=1"""
        args = []
        if student_id:
            sql += " AND t.student_id=?"; args.append(student_id)
        today = date.today().isoformat()
        if flt == "active": sql += " AND t.return_date IS NULL"
        elif flt == "overdue": sql += " AND t.return_date IS NULL AND t.due_date<?"; args.append(today)
        elif flt == "returned": sql += " AND t.return_date IS NOT NULL"
        rows = [dict(r) for r in self.db.query(sql + " ORDER BY t.id DESC", args)]
        for r in rows:  # live fine for unreturned overdue books
            r["overdue"] = r["return_date"] is None and r["due_date"] < today
            if r["return_date"] is None:
                r["fine"] = Transaction.fine_for(r["due_date"])
        return rows

    def students(self):
        return self.db.query("""SELECT u.id,u.name,u.email,s.roll_no,s.department,
            (SELECT COUNT(*) FROM transactions t WHERE t.student_id=u.id AND t.return_date IS NULL) issued
            FROM users u JOIN students s ON s.user_id=u.id ORDER BY u.name""")

    def stats(self):
        q = lambda s: self.db.one(s)[0]
        return {"total": q("SELECT COALESCE(SUM(quantity),0) FROM books"),
                "available": q("SELECT COALESCE(SUM(available),0) FROM books"),
                "issued": q("SELECT COUNT(*) FROM transactions WHERE return_date IS NULL"),
                "students": q("SELECT COUNT(*) FROM students"),
                "overdue": len(self.transactions("overdue"))}