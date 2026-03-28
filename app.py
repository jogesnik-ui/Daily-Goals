from flask import Flask, render_template, url_for, redirect, request
import sqlite3
from dotenv import load_dotenv
import os 
import json
from google import genai
from datetime import date, timedelta

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=api_key)

app = Flask(__name__)

def get_db():
    conn = sqlite3.connect("lockd.db")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY,
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
                   id INTEGER PRIMARY KEY,
                   current_streak INTEGER DEFAULT 0,
                   last_completion TEXT)      
                   
                   
                   """)
    cursor.execute("INSERT OR IGNORE INTO streaks (id, current_streak) VALUES(1, 0)")
    conn.commit()
    conn.close()


@app.route("/")
def index():
    conn = get_db()
    cursor = conn.cursor()
    today = date.today().isoformat()
    cursor.execute("SELECT * FROM tasks WHERE created_date = ?", (today,))
    tasks = cursor.fetchall()
    cursor.execute("SELECT * FROM streaks WHERE id = 1")
    streak = cursor.fetchone()

    total_points = cursor.execute(" SELECT SUM(points) FROM tasks WHERE done = 1").fetchone()[0] or 0


    done = cursor.execute("SELECT COUNT (*) FROM tasks WHERE created_date = ? AND done = 1" , (today,)).fetchone()[0]
    total = cursor.execute("SELECT COUNT (*) FROM tasks WHERE created_date = ? ", (today,)).fetchone()[0]

    percentage = round((done / total) * 100) if total != 0 else 0 



    return render_template("index.html", tasks=tasks, streak=streak, total_points=total_points, percentage=percentage)





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
    conn = get_db()
    cursor = conn.cursor()
    today = date.today().isoformat()
    cursor.execute("SELECT * FROM tasks WHERE created_date = ?", (today,))
    tasks = cursor.fetchall()
    cursor.execute("SELECT * FROM streaks WHERE id = 1")
    streak = cursor.fetchone()
    total_points = cursor.execute(" SELECT SUM(points) FROM tasks WHERE done = 1").fetchone()[0] or 0
    done = cursor.execute("SELECT COUNT (*) FROM tasks WHERE created_date = ? AND done = 1" , (today,)).fetchone()[0]
    total = cursor.execute("SELECT COUNT (*) FROM tasks WHERE created_date = ? ", (today,)).fetchone()[0]

    percentage = round((done / total) * 100) if total != 0 else 0 
    conn.close()
    priority = get_daily_recommendation(tasks)
    return render_template('index.html', tasks=tasks, priority=priority, streak = streak, total_points = total_points, percentage = percentage)


@app.route("/add", methods=['POST'])
def add_task():
    today = date.today().isoformat()
    task = request.form['task']
    description = request.form['description']
    category = request.form['category']
    points = estimate_points(task, description, category)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO tasks (task, description, category, points, done, created_date) VALUES (?, ?, ?, ?, ?, ?)",
                   (task, description, category, points, 0, today ))
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


def check_streak(conn, cursor):
    today = date.today().isoformat()
    done = cursor.execute("SELECT COUNT (*) FROM tasks WHERE created_date = ? AND done = 1" , (today,)).fetchone()[0]
    total = cursor.execute("SELECT COUNT (*) FROM tasks WHERE created_date = ? ", (today,)).fetchone()[0]

    if total == 0:
        return
    if done/total >= 0.8:
        streak = cursor.execute("SELECT *  FROM streaks WHERE id = 1").fetchone()
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

        cursor.execute("UPDATE streaks SET current_streak = ?, last_completion = ? WHERE id = 1",
                      (new_streak, today))
        
        conn.commit()



@app.route("/complete", methods=["POST"])
def complete_task():
        conn = get_db()
        cursor = conn.cursor()
        task_id = request.form['id']
        cursor.execute("UPDATE tasks SET done = ? WHERE id =?",
                   ( 1, task_id))
        check_streak(conn, cursor)
        conn.commit()
        conn.close()
        return redirect(url_for("index"))


@app.route("/delete", methods= ['POST'])
def delete_task():
    conn = get_db()
    cursor = conn.cursor()
    task_id = request.form['id']
    cursor.execute("DELETE FROM tasks WHERE id =?",
                   (task_id, ))
    conn.commit()
    conn.close()
    return redirect(url_for("index"))



if __name__ == "__main__":
    init_db()
    app.run(debug=True)



    
