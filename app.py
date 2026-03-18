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
# -----------------------------
_pool = None

def get_pool():
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL environment variable is not set.")
        _pool = SimpleConnectionPool(
            1, 5,
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
        model_path = os.path.join(BASE_DIR, "models", "model.pkl")
        encoder_path = os.path.join(BASE_DIR, "models", "label_encoder.pkl")
        with open(model_path, "rb") as f:
            model = pickle.load(f)
        with open(encoder_path, "rb") as f:
            label_encoder = pickle.load(f)
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
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT * FROM users WHERE email=%s", (email,))
            user = cur.fetchone()
            cur.close()
        finally:
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
    teachers = []
    parents = []
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT id, email FROM users WHERE role='Teacher'")
        teachers = cur.fetchall()
        cur.execute("SELECT id, email FROM users WHERE role='Parent'")
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
                    "INSERT INTO users(email, password, role, age, teacher_id, parent_id) VALUES(%s,%s,%s,%s,%s,%s)",
                    (email, hashed, role, age, teacher_id, parent_id),
                )
            else:
                cur.execute(
                    "INSERT INTO users(email, password, role, age) VALUES(%s,%s,%s,%s)",
                    (email, hashed, role, age),
                )

            conn.commit()
            cur.close()
            return redirect("/login")

        cur.close()
    finally:
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

    return redirect("/login")


# -----------------------------
# LOGOUT
# -----------------------------
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# -----------------------------
# HISTORY
# -----------------------------
@app.route("/history")
def history():
    if "user" not in session:
        return redirect("/login")

    results = []
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT * FROM results WHERE user_email = %s ORDER BY created_at DESC",
            (session["user"],)
        )
        results = cur.fetchall()
        cur.close()
    except Exception as e:
        print("HISTORY ERROR (table may not exist yet):", e)
        results = []
    finally:
        release_db_connection(conn)

    return render_template("history.html", results=results, user=session["user"])


# -----------------------------
# DEBUG — visit once to confirm model feature names
# -----------------------------
@app.route("/debug_model")
def debug_model():
    try:
        mdl, _ = load_model()
        return f"Model expects these {len(mdl.feature_names_in_)} features: {list(mdl.feature_names_in_)}"
    except Exception as e:
        return f"Error: {str(e)}"


# -----------------------------
# START TEST
# -----------------------------
@app.route("/start_cognitive")
def start_cognitive():
    if "user" not in session:
        return redirect("/login")
    return redirect("/symbolic_test")


# =========================================================
# SYMBOLIC COMPARISON TEST  (5 trials)
# =========================================================

@app.route("/symbolic_test")
def symbolic_test():
    if "user" not in session:
        return redirect("/login")
    session["symbolic_data"] = []
    session["symbolic_trial"] = 0
    session.modified = True
    return redirect("/symbolic_trial")


@app.route("/symbolic_trial")
def symbolic_trial():
    if "user" not in session:
        return redirect("/login")

    trial = session.get("symbolic_trial", 0)
    if trial >= 5:
        return redirect("/finish_symbolic")

    left = random.randint(1, 50)
    right = random.randint(1, 50)
    while left == right:
        right = random.randint(1, 50)

    session["left"] = left
    session["right"] = right
    session.modified = True

    return render_template("symbolic_test.html", left=left, right=right, trial=trial + 1)


@app.route("/submit_symbolic", methods=["POST"])
def submit_symbolic():
    if "user" not in session:
        return redirect("/login")

    choice = request.form["choice"]
    rt = float(request.form.get("response_time", 0))

    correct = "left" if session["left"] > session["right"] else "right"
    val = 1 if choice == correct else 0

    data = session.get("symbolic_data", [])
    data.append({"correct": val, "rt": rt})
    session["symbolic_data"] = data
    session["symbolic_trial"] = session.get("symbolic_trial", 0) + 1
    session.modified = True

    return redirect("/symbolic_trial")


@app.route("/finish_symbolic")
def finish_symbolic():
    if "user" not in session:
        return redirect("/login")

    trials = session.get("symbolic_data", [])
    if not trials:
        session["Accuracy_SymbolicComp"] = 0
        session["RTs_SymbolicComp"] = 0
    else:
        session["Accuracy_SymbolicComp"] = sum(t["correct"] for t in trials) / len(trials)
        session["RTs_SymbolicComp"] = sum(t["rt"] for t in trials) / len(trials)

    session.modified = True
    return redirect("/fraction_test")


# =========================================================
# FRACTION COMPARISON TEST  (5 trials)
# Template expects: {{ left }}, {{ right }}, {{ trial }}
# where left/right are fraction strings like "3/7"
# =========================================================

@app.route("/fraction_test")
def fraction_test():
    if "user" not in session:
        return redirect("/login")
    session["frac_data"] = []
    session["frac_trial"] = 0
    session.modified = True
    return redirect("/fraction_trial")


@app.route("/fraction_trial")
def fraction_trial():
    if "user" not in session:
        return redirect("/login")

    trial = session.get("frac_trial", 0)
    if trial >= 5:
        return redirect("/finish_fraction")

    def rand_fraction():
        num = random.randint(1, 9)
        den = random.randint(2, 10)
        while num >= den:
            num = random.randint(1, 9)
            den = random.randint(2, 10)
        return num, den

    ln, ld = rand_fraction()
    rn, rd = rand_fraction()
    while ln * rd == rn * ld:
        rn, rd = rand_fraction()

    # Store raw values for scoring
    session["frac_left_num"] = ln
    session["frac_left_den"] = ld
    session["frac_right_num"] = rn
    session["frac_right_den"] = rd
    session.modified = True

    # Template uses {{ left }} and {{ right }} as display strings
    return render_template(
        "fraction_test.html",
        left=f"{ln}/{ld}",
        right=f"{rn}/{rd}",
        trial=trial + 1
    )


@app.route("/submit_fraction", methods=["POST"])
def submit_fraction():
    if "user" not in session:
        return redirect("/login")

    choice = request.form["choice"]
    rt = float(request.form.get("response_time", 0))

    left_val = session["frac_left_num"] / session["frac_left_den"]
    right_val = session["frac_right_num"] / session["frac_right_den"]
    correct = "left" if left_val > right_val else "right"
    val = 1 if choice == correct else 0

    data = session.get("frac_data", [])
    data.append({"correct": val, "rt": rt})
    session["frac_data"] = data
    session["frac_trial"] = session.get("frac_trial", 0) + 1
    session.modified = True

    return redirect("/fraction_trial")


@app.route("/finish_fraction")
def finish_fraction():
    if "user" not in session:
        return redirect("/login")

    trials = session.get("frac_data", [])
    if not trials:
        session["Accuracy_Fraction"] = 0
        session["RTs_Fraction"] = 0
    else:
        session["Accuracy_Fraction"] = sum(t["correct"] for t in trials) / len(trials)
        session["RTs_Fraction"] = sum(t["rt"] for t in trials) / len(trials)

    session.modified = True
    return redirect("/ans_test")


# =========================================================
# ANS TEST  (5 trials)
# Template expects: {{ left }}, {{ right }}, {{ trial }}
# =========================================================

@app.route("/ans_test")
def ans_test():
    if "user" not in session:
        return redirect("/login")
    session["ans_data"] = []
    session["ans_trial"] = 0
    session.modified = True
    return redirect("/ans_trial")


@app.route("/ans_trial")
def ans_trial():
    if "user" not in session:
        return redirect("/login")

    trial = session.get("ans_trial", 0)
    if trial >= 5:
        return redirect("/finish_ans")

    left = random.randint(5, 30)
    right = random.randint(5, 30)
    while left == right:
        right = random.randint(5, 30)

    session["ans_left"] = left
    session["ans_right"] = right
    session.modified = True

    # Template uses {{ left }} and {{ right }}
    return render_template("ans_test.html", left=left, right=right, trial=trial + 1)


@app.route("/submit_ans", methods=["POST"])
def submit_ans():
    if "user" not in session:
        return redirect("/login")

    choice = request.form["choice"]
    rt = float(request.form.get("response_time", 0))

    correct = "left" if session["ans_left"] > session["ans_right"] else "right"
    val = 1 if choice == correct else 0

    data = session.get("ans_data", [])
    data.append({"correct": val, "rt": rt})
    session["ans_data"] = data
    session["ans_trial"] = session.get("ans_trial", 0) + 1
    session.modified = True

    return redirect("/ans_trial")


@app.route("/finish_ans")
def finish_ans():
    if "user" not in session:
        return redirect("/login")

    trials = session.get("ans_data", [])
    if not trials:
        session["Mean_ACC_ANS"] = 0
        session["Mean_RTs_ANS"] = 0
    else:
        session["Mean_ACC_ANS"] = sum(t["correct"] for t in trials) / len(trials)
        session["Mean_RTs_ANS"] = sum(t["rt"] for t in trials) / len(trials)

    session.modified = True
    return redirect("/wm_test")


# =========================================================
# WORKING MEMORY TEST  (5 trials)
# Template expects: {{ sequence }}, {{ trial }}
# where sequence is a space-separated string like "4 7 2 9"
# =========================================================

@app.route("/wm_test")
def wm_test():
    if "user" not in session:
        return redirect("/login")
    session["wm_data"] = []
    session["wm_trial"] = 0
    session.modified = True
    return redirect("/wm_trial")


@app.route("/wm_trial")
def wm_trial():
    if "user" not in session:
        return redirect("/login")

    trial = session.get("wm_trial", 0)
    if trial >= 5:
        return redirect("/finish_wm")

    span = random.randint(3, 6)
    digits = [random.randint(1, 9) for _ in range(span)]

    session["wm_digits"] = digits
    session.modified = True

    # Template uses {{ sequence }} as a display string
    sequence = " ".join(str(d) for d in digits)
    return render_template("wm_test.html", sequence=sequence, trial=trial + 1)


@app.route("/submit_wm", methods=["POST"])
def submit_wm():
    if "user" not in session:
        return redirect("/login")

    user_answer = request.form.get("answer", "").strip()
    rt = float(request.form.get("response_time", 0))
    correct_digits = session.get("wm_digits", [])

    try:
        user_digits = [int(d) for d in user_answer.split()]
    except ValueError:
        user_digits = []

    val = 1 if user_digits == correct_digits else 0

    data = session.get("wm_data", [])
    data.append({"correct": val, "rt": rt, "span": len(correct_digits)})
    session["wm_data"] = data
    session["wm_trial"] = session.get("wm_trial", 0) + 1
    session.modified = True

    return redirect("/wm_trial")


@app.route("/finish_wm")
def finish_wm():
    if "user" not in session:
        return redirect("/login")

    trials = session.get("wm_data", [])
    if not trials:
        session["wm_K"] = 0
    else:
        correct_trials = [t for t in trials if t["correct"] == 1]
        session["wm_K"] = (
            sum(t["span"] for t in correct_trials) / len(correct_trials)
            if correct_trials else 0
        )

    session.modified = True
    return redirect("/final_prediction")


# =========================================================
# FINAL PREDICTION
# =========================================================

@app.route("/final_prediction")
def final_prediction():
    if "user" not in session:
        return redirect("/login")

    risk = "Unknown"
    confidence = 0
    recommendations = ""

    try:
        mdl, le = load_model()
        import pandas as pd

        # Read exact feature names the model was trained with
        feature_names = list(mdl.feature_names_in_)
        print("MODEL EXPECTS:", feature_names)

        all_values = {
            "Mean_ACC_ANS":          session.get("Mean_ACC_ANS", 0),
            "Mean_RTs_ANS":          session.get("Mean_RTs_ANS", 0),
            "wm_K":                  session.get("wm_K", 0),
            "Accuracy_SymbolicComp": session.get("Accuracy_SymbolicComp", 0),
            "RTs_SymbolicComp":      session.get("RTs_SymbolicComp", 0),
            "Accuracy_Fraction":     session.get("Accuracy_Fraction", 0),
            "RTs_Fraction":          session.get("RTs_Fraction", 0),
        }

        # Only pass what the model expects, in the right order
        row = {k: all_values.get(k, 0) for k in feature_names}
        features = pd.DataFrame([row], columns=feature_names)
        print("FEATURES SENT TO MODEL:", features.to_dict())

        prediction = mdl.predict(features)
        probability = mdl.predict_proba(features)

        label = le.inverse_transform(prediction)[0].lower()
        confidence = round(max(probability[0]) * 100, 2)

        if "dyscalculia" in label:
            risk = "Dyscalculia Detected"
            recommendations = (
                "Consider consulting a specialist. "
                "Use visual aids, number lines, and hands-on manipulatives. "
                "Break problems into smaller steps and allow extra time."
            )
        else:
            risk = "No Dyscalculia Detected"
            recommendations = (
                "Performance looks typical. "
                "Continue regular practice to strengthen numeracy skills."
            )

    except FileNotFoundError:
        risk = "Model Not Found"
        confidence = 0
        recommendations = "Please ensure model files exist in the /models directory."
    except Exception as e:
        print("PREDICTION ERROR:", e)
        risk = "Prediction Error"
        confidence = 0
        recommendations = f"An error occurred: {str(e)}"

    return render_template(
        "final_result.html",
        risk=risk,
        confidence=confidence,
        recommendations=recommendations
    )


# =========================================================
# RUN APP
# =========================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)