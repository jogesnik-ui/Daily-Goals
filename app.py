from flask import Flask, render_template, url_for, redirect, request, session
import sqlite3
from dotenv import load_dotenv
import os 
import json
from google import genai
from datetime import date, timedelta
import bcrypt
import psycopg2
from psycopg2.extras import RealDictCursor

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

client = genai.Client(api_key=api_key)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")
def get_db():
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    conn.cursor_factory = RealDictCursor
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
                   id SERIAL PRIMARY KEY ,
                   username TEXT UNIQUE NOT NULL,
                   password BYTEA NOT NULL)
                   """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        user_id INTEGER,
        id SERIAL PRIMARY KEY,
        task TEXT,
        description TEXT,
        category TEXT,
        points INTEGER DEFAULT 0,
        done INTEGER DEFAULT 0,
        created_date TEXT
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS streaks (
                   user_id INTEGER UNIQUE,
                   id SERIAL PRIMARY KEY,
                   current_streak INTEGER DEFAULT 0,
                   last_completion TEXT,
                   FOREIGN KEY (user_id) REFERENCES users(id)
                   )

                   """)



    conn.commit()
    conn.close()

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        hashed_pw = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())

        conn = get_db()
        cursor = conn.cursor()


        try:
            cursor.execute("INSERT INTO users (username, password) VALUES(%s, %s) RETURNING id",
                           (username, hashed_pw)
                        )
            user_id = cursor.fetchone()['id']
            cursor.execute("INSERT INTO streaks (user_id, current_streak) VALUES (%s, 0)",
                            (user_id,)
                        )

            conn.commit()
            return redirect("/login")
        
        except psycopg2.errors.UniqueViolation:
            return "Username already exists"
        
        finally: 
            conn.close()

    return render_template("register.html")



@app.route("/login", methods=["GET", "POST"])

def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM users WHERE username = %s",
                       (username, ))
        
        user = cursor.fetchone()
        conn.close()
        error = None
        if user:
            stored_pw = user["password"]
            if bcrypt.checkpw(password.encode("utf-8"), stored_pw):
                session["user_id"] = user["id"]
                session["username"] = user["username"]
                return redirect("/")
        error = "Invalid Credentials"
        return render_template("login.html", error=error)
    return render_template("login.html")
        
@app.route("/")
def index():

    if "user_id" not in session:
        return redirect("/login")
    
    conn = get_db()
    cursor = conn.cursor()
    today = date.today().isoformat()
    cursor.execute("SELECT * FROM tasks WHERE created_date = %s AND user_id = %s ", (today, session["user_id"]))
    tasks = cursor.fetchall()
    cursor.execute("SELECT * FROM streaks WHERE user_id = %s", (session["user_id"],))
    streak = cursor.fetchone()

    total_points = cursor.execute(" SELECT SUM(points) FROM tasks WHERE done = 1 AND user_id = %s", (session["user_id"], )).fetchone()[0] or 0


    done = cursor.execute("SELECT COUNT (*) FROM tasks WHERE created_date = %s AND done = 1 AND user_id = %s" , (today, session["user_id"], )).fetchone()[0]
    total = cursor.execute("SELECT COUNT (*) FROM tasks WHERE created_date = %s AND user_id = %s", (today, session["user_id"],)).fetchone()[0]

    percentage = round((done / total) * 100) if total != 0 else 0 



    return render_template("index.html", tasks=tasks, streak=streak, total_points=total_points, percentage=percentage, username=session["username"])





def estimate_points(task, description, category):
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=f"""Rate this task's difficutly and assign points.
Task: {task}
Description: {description}
Category: {category}

Return JSON only, no other text, no markdown code blocks:
{{"points": <number between 5 and 50>, "difficulty": "easy/medium/hard"}}
    """)
    print("RAW RESPONSE:", repr(response.text)) 
    data = json.loads(response.text)
    return data["points"]

def format_tasks(tasks):
    task_list = []
    for task in tasks:
        if not task['done']:
            task_list.append(f"{task['task']} - {task['description']}")
    return "\n".join(task_list)

def get_daily_recommendation(tasks):
    formatted = format_tasks(tasks)
    chat = client.chats.create(model="gemini-2.5-flash")
    response = chat.send_message(
        f"These are my tasks for today:\n{formatted}\nWhich should I focus on first and why? Plain text, 3 sentences, no markdown."
    )
    return response.text

@app.route("/nudge")
def nudge():
    if "user_id" not in session:
        return redirect("/login")
    conn = get_db()
    cursor = conn.cursor()
    today = date.today().isoformat()
    cursor.execute("SELECT * FROM tasks WHERE created_date = %s AND user_id=%s ", (today, session["user_id"]))
    tasks = cursor.fetchall()
    cursor.execute("SELECT * FROM streaks WHERE user_id=%s", (session["user_id"],))
    streak = cursor.fetchone()
    total_points = cursor.execute(" SELECT SUM(points) FROM tasks WHERE done = 1 AND user_id = %s", (session["user_id"],)).fetchone()[0] or 0
    done = cursor.execute("SELECT COUNT (*) FROM tasks WHERE created_date = %s AND done = 1 AND user_id = %s" , (today, session["user_id"],)).fetchone()[0]
    total = cursor.execute("SELECT COUNT (*) FROM tasks WHERE created_date = %s AND user_id = %s", (today, session["user_id"],)).fetchone()[0]

    percentage = round((done / total) * 100) if total != 0 else 0 
    conn.close()
    priority = get_daily_recommendation(tasks)
    return render_template('index.html', tasks=tasks, priority=priority, streak = streak, total_points = total_points, percentage = percentage)


@app.route("/add", methods=['POST'])
def add_task():
    if "user_id" not in session:
        return redirect("/login")
    today = date.today().isoformat()
    task = request.form['task']
    description = request.form['description']
    category = request.form['category']
    points = estimate_points(task, description, category)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO tasks (user_id, task, description, category, points, done, created_date) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                   (session["user_id"], task, description, category, points, 0, today ))
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


def check_streak(conn, cursor):
    today = date.today().isoformat()
    done = cursor.execute("SELECT COUNT (*) FROM tasks WHERE created_date = %s AND done = 1 AND user_id = %s" , (today, session["user_id"],)).fetchone()[0]
    total = cursor.execute("SELECT COUNT (*) FROM tasks WHERE created_date = %s AND user_id = %s", (today, session["user_id"],)).fetchone()[0]

    if total == 0:
        return
    if done/total >= 0.8:
        streak = cursor.execute("SELECT *  FROM streaks WHERE user_id = %s", 
                                (session["user_id"],)
                                ).fetchone()
        last = streak['last_completion']
        current = streak['current_streak']
        print(f"Total:{total}, Done:{done}")

        if last == today:
            return

        yesterday = (date.today() - timedelta(days =1)).isoformat()

        if last == yesterday:
            new_streak = current + 1
        else:
            new_streak = 1

        cursor.execute("UPDATE streaks SET current_streak = %s, last_completion = %s WHERE user_id = %s",
                      (new_streak, today, session["user_id"]))
        
        conn.commit()



@app.route("/complete", methods=["POST"])
def complete_task():
        if "user_id" not in session:
            return redirect("/login")
        conn = get_db()
        cursor = conn.cursor()
        task_id = request.form['id']
        cursor.execute("UPDATE tasks SET done = %s WHERE id =%s AND user_id = %s" ,
                   ( 1, task_id, session["user_id"]))
        check_streak(conn, cursor)
        conn.commit()
        conn.close()
        return redirect(url_for("index"))


@app.route("/delete", methods= ['POST'])
def delete_task():
    if "user_id" not in session:
        return redirect("/login")
    conn = get_db()
    cursor = conn.cursor()
    task_id = request.form['id']
    cursor.execute("DELETE FROM tasks WHERE id =%s AND user_id = %s",
                   (task_id, session["user_id"]))
    conn.commit()
    conn.close()
    return redirect(url_for("index"))



@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


init_db()
if __name__ == "__main__":
    app.run(debug=True)



    
