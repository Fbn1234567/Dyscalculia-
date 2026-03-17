from flask import Flask, render_template, request, redirect, session
from flask_bcrypt import Bcrypt
import random
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import SimpleConnectionPool

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "supersecretkey")

bcrypt = Bcrypt(app)

DATABASE_URL = os.getenv("DATABASE_URL")

# -----------------------------
# DATABASE CONNECTION (LAZY POOL)
# Pool is created on first request so a missing/bad DATABASE_URL
# doesn't crash the entire app at startup.
# -----------------------------
_pool = None

def get_pool():
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL environment variable is not set.")
        _pool = SimpleConnectionPool(
            1,
            5,
            dsn=DATABASE_URL,
            sslmode="require",
            connect_timeout=5
        )
    return _pool

def get_db_connection():
    return get_pool().getconn()

def release_db_connection(conn):
    get_pool().putconn(conn)


# -----------------------------
# ML MODEL LAZY LOADING
# -----------------------------
model = None
label_encoder = None

def load_model():
    global model, label_encoder

    if model is None:
        import pickle
        BASE_DIR = os.path.dirname(os.path.abspath(__file__))

        model = pickle.load(open(os.path.join(BASE_DIR, "models", "model.pkl"), "rb"))
        label_encoder = pickle.load(open(os.path.join(BASE_DIR, "models", "label_encoder.pkl"), "rb"))

    return model, label_encoder


# -----------------------------
# HOME
# -----------------------------
@app.route("/")
def home():
    return redirect("/login")


# -----------------------------
# LOGIN
# -----------------------------
@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form["email"]
        password = request.form["password"]

        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("SELECT * FROM users WHERE email=%s", (email,))
        user = cur.fetchone()

        cur.close()
        release_db_connection(conn)

        if user and bcrypt.check_password_hash(user["password"], password):

            session["user"] = user["email"]
            session["role"] = user["role"]
            session["age"] = int(user.get("age") or 0)

            return redirect("/dashboard")

        return render_template("login.html", error="Invalid credentials")

    return render_template("login.html")


# -----------------------------
# REGISTER
# -----------------------------
@app.route("/register", methods=["GET", "POST"])
def register():

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("SELECT id,email FROM users WHERE role='Teacher'")
    teachers = cur.fetchall()

    cur.execute("SELECT id,email FROM users WHERE role='Parent'")
    parents = cur.fetchall()

    if request.method == "POST":

        email = request.form["email"]
        password = request.form["password"]
        role = request.form["role"]
        age = int(request.form["age"])

        teacher_id = request.form.get("teacher_id")
        parent_id = request.form.get("parent_id")

        hashed = bcrypt.generate_password_hash(password).decode("utf-8")

        if role == "Student":

            cur.execute(
                """
                INSERT INTO users(email,password,role,age,teacher_id,parent_id)
                VALUES(%s,%s,%s,%s,%s,%s)
                """,
                (email, hashed, role, age, teacher_id, parent_id),
            )

        else:

            cur.execute(
                """
                INSERT INTO users(email,password,role,age)
                VALUES(%s,%s,%s,%s)
                """,
                (email, hashed, role, age),
            )

        conn.commit()
        cur.close()
        release_db_connection(conn)

        return redirect("/login")

    cur.close()
    release_db_connection(conn)

    return render_template("register.html", teachers=teachers, parents=parents)


# -----------------------------
# DASHBOARD
# -----------------------------
@app.route("/dashboard")
def dashboard():

    if "user" not in session:
        return redirect("/login")

    role = session["role"]

    if role == "Student":
        return render_template("student_dashboard.html", user=session["user"])

    if role == "Teacher":
        return render_template("teacher_dashboard.html", user=session["user"])

    if role == "Parent":
        return render_template("parent_dashboard.html", user=session["user"])

    if role == "Admin":
        return render_template("admin_dashboard.html", user=session["user"])


# -----------------------------
# LOGOUT
# -----------------------------
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# -----------------------------
# CREATE TEACHER
# -----------------------------
@app.route("/create_teacher", methods=["GET", "POST"])
def create_teacher():

    if request.method == "POST":

        email = request.form["email"]
        password = request.form["password"]

        hashed = bcrypt.generate_password_hash(password).decode("utf-8")

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO users(email,password,role)
            VALUES(%s,%s,'Teacher')
            """,
            (email, hashed),
        )

        conn.commit()
        cur.close()
        release_db_connection(conn)

        return redirect("/dashboard")

    return render_template("create_teacher.html")


# -----------------------------
# START TEST
# -----------------------------
@app.route("/start_cognitive")
def start_cognitive():

    if "user" not in session:
        return redirect("/login")

    return redirect("/symbolic_test")


# -----------------------------
# SYMBOLIC TEST
# -----------------------------
@app.route("/symbolic_test")
def symbolic_test():
    session["symbolic_data"] = []
    session["symbolic_trial"] = 0
    return redirect("/symbolic_trial")


@app.route("/symbolic_trial")
def symbolic_trial():

    trial = session.get("symbolic_trial", 0)

    if trial >= 5:
        return redirect("/finish_symbolic")

    left = random.randint(1, 50)
    right = random.randint(1, 50)

    while left == right:
        right = random.randint(1, 50)

    session["left"] = left
    session["right"] = right

    return render_template("symbolic_test.html", left=left, right=right, trial=trial + 1)


@app.route("/submit_symbolic", methods=["POST"])
def submit_symbolic():

    try:
        choice = request.form["choice"]
        rt = float(request.form.get("response_time", 0))

        left = session.get("left")
        right = session.get("right")

        if left is None or right is None:
            return redirect("/symbolic_test")

        correct = "left" if left > right else "right"
        correct_val = 1 if choice == correct else 0

        symbolic_data = session.get("symbolic_data", [])
        symbolic_data.append({"correct": correct_val, "rt": rt})
        session["symbolic_data"] = symbolic_data          # reassign to mark session dirty
        session["symbolic_trial"] = session.get("symbolic_trial", 0) + 1

        return redirect("/symbolic_trial")

    except Exception as e:
        app.logger.error(f"submit_symbolic error: {e}")
        return redirect("/symbolic_test")


@app.route("/finish_symbolic")
def finish_symbolic():

    trials = session.get("symbolic_data", [])

    if not trials:
        return redirect("/symbolic_test")

    accuracy = sum(t["correct"] for t in trials) / len(trials)
    mean_rt = sum(t["rt"] for t in trials) / len(trials)

    session["Accuracy_SymbolicComp"] = accuracy
    session["RTs_SymbolicComp"] = mean_rt

    return redirect("/fraction_test")


# -----------------------------
# FRACTION TEST
# -----------------------------
@app.route("/fraction_test")
def fraction_test():
    session["frac_data"] = []
    session["frac_trial"] = 0
    return redirect("/fraction_trial")


@app.route("/fraction_trial")
def fraction_trial():

    trial = session.get("frac_trial", 0)

    if trial >= 5:
        return redirect("/finish_fraction")

    a = random.randint(1, 9)
    b = random.randint(2, 10)
    c = random.randint(1, 9)
    d = random.randint(2, 10)

    while a / b == c / d:
        c = random.randint(1, 9)
        d = random.randint(2, 10)

    session["frac_left"] = [a, b]     # list instead of tuple — JSON-safe
    session["frac_right"] = [c, d]

    return render_template(
        "fraction_test.html",
        left=f"{a}/{b}",
        right=f"{c}/{d}",
        trial=trial + 1,
    )


@app.route("/submit_fraction", methods=["POST"])
def submit_fraction():

    try:
        choice = request.form["choice"]
        rt = float(request.form.get("response_time", 0))

        frac_left = session.get("frac_left")
        frac_right = session.get("frac_right")

        if frac_left is None or frac_right is None:
            return redirect("/fraction_test")

        a, b = frac_left
        c, d = frac_right

        correct = "left" if a / b > c / d else "right"
        correct_val = 1 if choice == correct else 0

        frac_data = session.get("frac_data", [])
        frac_data.append({"correct": correct_val, "rt": rt})
        session["frac_data"] = frac_data          # reassign to mark session dirty
        session["frac_trial"] = session.get("frac_trial", 0) + 1

        return redirect("/fraction_trial")

    except Exception as e:
        app.logger.error(f"submit_fraction error: {e}")
        return redirect("/fraction_test")


@app.route("/finish_fraction")
def finish_fraction():

    trials = session.get("frac_data", [])

    if not trials:
        return redirect("/fraction_test")

    accuracy = sum(t["correct"] for t in trials) / len(trials)
    mean_rt = sum(t["rt"] for t in trials) / len(trials)

    session["Accuracy_Fraction"] = accuracy
    session["RTs_Fraction"] = mean_rt

    if session.get("age", 0) < 10:
        return redirect("/final_prediction")

    return redirect("/ans_test")


# -----------------------------
# ANS TEST
# -----------------------------
@app.route("/ans_test")
def ans_test():
    session["ans_data"] = []
    session["ans_trial"] = 0
    return redirect("/ans_trial")


@app.route("/ans_trial")
def ans_trial():

    trial = session.get("ans_trial", 0)

    if trial >= 5:
        return redirect("/finish_ans")

    left = random.randint(5, 20)
    right = random.randint(5, 20)

    while left == right:
        right = random.randint(5, 20)

    session["ans_left"] = left
    session["ans_right"] = right

    return render_template("ans_test.html", left=left, right=right, trial=trial + 1)


@app.route("/submit_ans", methods=["POST"])
def submit_ans():

    try:
        choice = request.form["choice"]
        rt = float(request.form.get("response_time", 0))

        left = session.get("ans_left")
        right = session.get("ans_right")

        if left is None or right is None:
            return redirect("/ans_test")

        correct = "left" if left > right else "right"
        correct_val = 1 if choice == correct else 0

        ans_data = session.get("ans_data", [])
        ans_data.append({"correct": correct_val, "rt": rt})
        session["ans_data"] = ans_data          # reassign to mark session dirty
        session["ans_trial"] = session.get("ans_trial", 0) + 1

        return redirect("/ans_trial")

    except Exception as e:
        app.logger.error(f"submit_ans error: {e}")
        return redirect("/ans_test")


@app.route("/finish_ans")
def finish_ans():

    trials = session.get("ans_data", [])

    if not trials:
        return redirect("/ans_test")

    accuracy = sum(t["correct"] for t in trials) / len(trials)
    mean_rt = sum(t["rt"] for t in trials) / len(trials)

    session["Mean_ACC_ANS"] = accuracy
    session["Mean_RTs_ANS"] = mean_rt

    return redirect("/wm_test")


# -----------------------------
# WORKING MEMORY TEST
# -----------------------------
@app.route("/wm_test")
def wm_test():
    session["wm_level"] = 3
    session["wm_data"] = []
    return redirect("/wm_trial")


@app.route("/wm_trial")
def wm_trial():

    level = session.get("wm_level", 3)

    sequence = [str(random.randint(1, 9)) for _ in range(level)]
    session["sequence"] = sequence

    return render_template("wm_test.html", sequence=" ".join(sequence))


@app.route("/submit_wm", methods=["POST"])
def submit_wm():

    answer = request.form["answer"].replace(" ", "")
    correct_seq = "".join(session.get("sequence", []))

    correct = 1 if answer == correct_seq else 0

    wm_data = session.get("wm_data", [])
    wm_data.append({"level": session.get("wm_level", 3), "correct": correct})
    session["wm_data"] = wm_data          # reassign to mark session dirty

    if correct:
        session["wm_level"] = session.get("wm_level", 3) + 1
        return redirect("/wm_trial")

    return redirect("/finish_wm")


@app.route("/finish_wm")
def finish_wm():

    data = session.get("wm_data", [])
    scores = [d["level"] for d in data if d["correct"] == 1]

    session["wm_K"] = max(scores) if scores else 0

    return redirect("/final_prediction")


# -----------------------------
# FINAL ML PREDICTION
# -----------------------------
@app.route("/final_prediction")
def final_prediction():

    model, label_encoder = load_model()

    import numpy as np

    features = np.array([
        [
            session.get("Mean_ACC_ANS", 0),
            session.get("Mean_RTs_ANS", 0),
            session.get("wm_K", 0),
            session.get("Accuracy_SymbolicComp", 0),
            session.get("RTs_SymbolicComp", 0),
            session.get("Accuracy_Fraction", 0),
            session.get("RTs_Fraction", 0),
        ]
    ])

    prediction = model.predict(features)
    probability = model.predict_proba(features)

    label = label_encoder.inverse_transform(prediction)[0].lower()
    confidence = round(max(probability[0]) * 100, 2)

    if label in ["dd", "severe", "high"]:
        risk = "Highest Risk"
        rec = "Immediate professional evaluation recommended."
    elif label in ["moderate", "medium"]:
        risk = "Medium Risk"
        rec = "Provide additional math practice and monitoring."
    elif label in ["mild", "low"]:
        risk = "Lowest Risk"
        rec = "Provide reinforcement activities."
    else:
        risk = "No Dyscalculia Detected"
        rec = "Continue normal learning."

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO results(student_email,ans_acc,ans_rt,wm_k,sym_acc,sym_rt,risk_level)
        VALUES(%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            session["user"],
            session.get("Mean_ACC_ANS", 0),
            session.get("Mean_RTs_ANS", 0),
            session.get("wm_K", 0),
            session.get("Accuracy_SymbolicComp", 0),
            session.get("RTs_SymbolicComp", 0),
            risk,
        ),
    )

    conn.commit()
    cur.close()
    release_db_connection(conn)

    return render_template(
        "final_result.html",
        risk=risk,
        confidence=confidence,
        recommendations=rec,
    )


# -----------------------------
# HISTORY
# -----------------------------
@app.route("/history")
def history():

    if "user" not in session:
        return redirect("/login")

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute(
        """
        SELECT ans_acc,ans_rt,wm_k,sym_acc,sym_rt,risk_level,created_at
        FROM results
        WHERE student_email=%s
        ORDER BY created_at DESC
        """,
        (session["user"],),
    )

    results = cur.fetchall()

    cur.close()
    release_db_connection(conn)

    return render_template("history.html", results=results)


# -----------------------------
# TEACHER RESULTS
# -----------------------------
@app.route("/teacher_results")
def teacher_results():

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute(
        """
        SELECT student_email,risk_level,created_at
        FROM results
        ORDER BY created_at DESC
        """
    )

    results = cur.fetchall()

    cur.close()
    release_db_connection(conn)

    return render_template("teacher_results.html", results=results)


# -----------------------------
# RUN APP
# -----------------------------
if __name__ == "__main__":
    app.run()