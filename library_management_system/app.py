from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash
from database import DatabaseManager
from models import Library, Book, User, LibraryError, Admin

app = Flask(__name__)
app.secret_key = "lms-9f3a7c1e5b24d8a6"  # change for production
db = DatabaseManager("library.db")
db.initialize()
lib = Library(db)


def current_user():
    r = session.get("uid") and db.one("SELECT * FROM users WHERE id=?", (session["uid"],))
    return User.from_row(r) if r else None


def login_required(role=None):
    def deco(f):
        @wraps(f)
        def wrapper(*a, **k):
            u = current_user()
            if not u:
                return redirect(url_for("login"))
            if role and u.role != role:
                flash("You do not have access to that page.", "danger")
                return redirect(url_for("dashboard"))
            return f(u, *a, **k)
        return wrapper
    return deco


@app.context_processor
def inject():
    return {"user": current_user()}


@app.route("/")
def index():
    return redirect(url_for("dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = lib.authenticate(request.form["email"], request.form["password"])
        if u:
            session["uid"] = u.id
            return redirect(url_for("dashboard"))
        flash("Invalid email or password.", "danger")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        f = request.form
        try:
            lib.register_student(f["name"], f["email"], f["password"], f["roll_no"], f["department"])
            flash("Registration successful. Please log in.", "success")
            return redirect(url_for("login"))
        except LibraryError as e:
            flash(str(e), "danger")
    return render_template("register.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required()
def dashboard(u):
    if isinstance(u, Admin):
        return render_template("dashboard.html", stats=lib.stats(), recent=lib.transactions()[:6])
    mine = lib.transactions(student_id=u.id)
    return render_template("dashboard.html", mine=[t for t in mine if not t["return_date"]],
                           fines=sum(t["fine"] for t in mine), count=len(mine))


@app.route("/books")
@login_required()
def books(u):
    q, c = request.args.get("q", ""), request.args.get("category", "")
    return render_template("books.html", books=lib.search_books(q, c), cats=lib.categories(), q=q, c=c)


@app.route("/books/save", methods=["POST"])
@login_required("admin")
def save_book(u):
    f = request.form
    try:
        lib.save_book(Book(f["title"], f["author"], f["isbn"], f["category"], f["quantity"], bid=int(f["id"]) if f.get("id") else None))
        flash("Book saved.", "success")
    except (LibraryError, ValueError) as e:
        flash(str(e) if isinstance(e, LibraryError) else "Quantity must be a number.", "danger")
    return redirect(url_for("books"))


@app.route("/books/<int:bid>/delete", methods=["POST"])
@login_required("admin")
def delete_book(u, bid):
    try:
        lib.delete_book(bid); flash("Book deleted.", "success")
    except LibraryError as e:
        flash(str(e), "danger")
    return redirect(url_for("books"))


@app.route("/books/<int:bid>/borrow", methods=["POST"])
@login_required("student")
def borrow(u, bid):
    try:
        lib.issue_book(bid, u.id); flash("Book borrowed. Check your due date on the dashboard.", "success")
    except LibraryError as e:
        flash(str(e), "danger")
    return redirect(url_for("books"))


@app.route("/transactions")
@login_required()
def transactions(u):
    flt = request.args.get("filter", "all")
    rows = lib.transactions(flt, None if isinstance(u, Admin) else u.id)
    return render_template("transactions.html", rows=rows, flt=flt, books=lib.search_books() if isinstance(u, Admin) else [],
                           students=lib.students() if isinstance(u, Admin) else [])


@app.route("/issue", methods=["POST"])
@login_required("admin")
def issue(u):
    try:
        lib.issue_book(int(request.form["book_id"]), int(request.form["student_id"])); flash("Book issued.", "success")
    except LibraryError as e:
        flash(str(e), "danger")
    return redirect(url_for("transactions"))


@app.route("/return/<int:tid>", methods=["POST"])
@login_required("admin")
def return_(u, tid):
    try:
        fine = lib.return_book(tid); flash(f"Book returned. Fine: Rs. {fine}", "success")
    except LibraryError as e:
        flash(str(e), "danger")
    return redirect(url_for("transactions"))


@app.route("/students")
@login_required("admin")
def students(u):
    return render_template("students.html", students=lib.students())


if __name__ == "__main__":
    app.run(debug=True)