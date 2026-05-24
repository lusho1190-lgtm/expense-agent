from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse
import os, re, psycopg2
from datetime import datetime

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")
MONTHLY_BUDGET = 12000
WEEKLY_BUDGET  = 900
FIXED_TOTAL = 8035

def get_conn():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id BIGSERIAL PRIMARY KEY,
            description TEXT NOT NULL,
            amount DECIMAL(10,2) NOT NULL,
            user_name TEXT NOT NULL DEFAULT 'Luis',
            month_key TEXT NOT NULL,
            week_num INTEGER NOT NULL,
            year INTEGER NOT NULL,
            fixed BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    conn.commit()
    cur.close()
    conn.close()

def month_key():
    d = datetime.now()
    return f"{d.year}-{d.month:02d}"

def week_number():
    return datetime.now().isocalendar()[1]

def fmt(n):
    return f"${abs(n):,.2f}"

def parse_expense(text):
    text = text.strip()
    patterns = [
        r'^(.+?)\s+(\d+(?:\.\d+)?)\s+(luis|claudia)$',
        r'^(.+?)\s+(\d+(?:\.\d+)?)$',
    ]
    for p in patterns:
        m = re.match(p, text, re.IGNORECASE)
        if m:
            groups = m.groups()
            desc = groups[0].strip().capitalize()
            amt  = float(groups[1])
            user = groups[2].capitalize() if len(groups) > 2 else "Luis"
            return desc, amt, user
    return None, None, None

def get_weekly_spent():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE week_num=%s AND year=%s AND fixed=FALSE",
                (week_number(), datetime.now().year))
    result = cur.fetchone()[0]
    cur.close(); conn.close()
    return float(result)

def get_monthly_totals():
    conn = get_conn()
    cur = conn.cursor()
    mk = month_key()
    cur.execute("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE month_key=%s AND fixed=FALSE", (mk,))
    total_var = float(cur.fetchone()[0])
    cur.execute("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE month_key=%s AND fixed=FALSE AND user_name='Luis'", (mk,))
    luis = float(cur.fetchone()[0])
    cur.execute("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE month_key=%s AND fixed=FALSE AND user_name='Claudia'", (mk,))
    claudia = float(cur.fetchone()[0])
    cur.close(); conn.close()
    return total_var, luis, claudia

def get_last_expenses(limit=8):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT description, amount, user_name FROM expenses WHERE month_key=%s AND fixed=FALSE ORDER BY created_at DESC LIMIT %s",
                (month_key(), limit))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return rows

def delete_last():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, description, amount, user_name FROM expenses WHERE month_key=%s AND fixed=FALSE ORDER BY created_at DESC LIMIT 1",
                (month_key(),))
    row = cur.fetchone()
    if row:
        cur.execute("DELETE FROM expenses WHERE id=%s", (row[0],))
        conn.commit()
    cur.close(); conn.close()
    return row

@app.route("/sms", methods=["POST"])
def sms():
    body = request.form.get("Body", "").strip()
    resp = MessagingResponse()
    cmd = body.lower().strip()

    if cmd in ["ayuda", "help", "?"]:
        resp.message(
            "💰 Control de Gastos\n\n"
            "Agregar gasto:\n"
            "  Gasolina 60 Luis\n"
            "  Comida 45 Claudia\n"
            "  Target 120\n\n"
            "Comandos:\n"
            "  semana → resumen semanal\n"
            "  mes → resumen del mes\n"
            "  lista → ultimos gastos\n"
            "  borrar → eliminar ultimo\n"
            "  ayuda → este menu"
        )
        return Response(str(resp), mimetype="text/xml")

    if cmd in ["semana", "week", "semanal"]:
        spent = get_weekly_spent()
        left  = WEEKLY_BUDGET - spent
        pct   = min(int((spent / WEEKLY_BUDGET) * 100), 100)
        bar   = "█" * (pct // 10) + "░" * (10 - pct // 10)
        status = "✅" if left >= 0 else "🔴"
        resp.message(
            f"📅 Esta Semana\n{bar} {pct}%\n\n"
            f"Gastado: {fmt(spent)}\nMeta: {fmt(WEEKLY_BUDGET)}\n"
            f"{status} {'Queda' if left >= 0 else 'Excedido'}: {fmt(left)}"
        )
        return Response(str(resp), mimetype="text/xml")

    if cmd in ["mes", "month", "resumen"]:
        var, luis, claudia = get_monthly_totals()
        total = FIXED_TOTAL + var
        left  = MONTHLY_BUDGET - total
        pct   = min(int((total / MONTHLY_BUDGET) * 100), 100)
        bar   = "█" * (pct // 10) + "░" * (10 - pct // 10)
        status = "✅" if left >= 0 else "🔴"
        resp.message(
            f"📊 Resumen del Mes\n{bar} {pct}%\n\n"
            f"Fijos: {fmt(FIXED_TOTAL)}\nLuis: {fmt(luis)}\n"
            f"Claudia: {fmt(claudia)}\nTotal: {fmt(total)}\n"
            f"Presupuesto: {fmt(MONTHLY_BUDGET)}\n\n"
            f"{status} {'Disponible' if left >= 0 else 'Excedido'}: {fmt(left)}"
        )
        return Response(str(resp), mimetype="text/xml")

    if cmd in ["lista", "list", "gastos"]:
        rows = get_last_expenses()
        if not rows:
            resp.message("No hay gastos este mes.")
            return Response(str(resp), mimetype="text/xml")
        lines = ["📋 Ultimos gastos:\n"]
        for desc, amt, user in rows:
            lines.append(f"• {desc} {fmt(float(amt))} — {user}")
        resp.message("\n".join(lines))
        return Response(str(resp), mimetype="text/xml")

    if cmd in ["borrar", "delete", "undo", "deshacer"]:
        row = delete_last()
        if row:
            resp.message(f"🗑 Eliminado: {row[1]} {fmt(float(row[2]))} — {row[3]}")
        else:
            resp.message("No hay gastos para eliminar.")
        return Response(str(resp), mimetype="text/xml")

    desc, amt, user = parse_expense(body)
    if desc and amt:
        conn = get_conn()
        cur = conn.cursor()
        now = datetime.now()
        cur.execute(
            "INSERT INTO expenses (description, amount, user_name, month_key, week_num, year, fixed) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (desc, amt, user, month_key(), week_number(), now.year, False)
        )
        conn.commit()
        cur.close(); conn.close()
        spent = get_weekly_spent()
        left  = WEEKLY_BUDGET - spent
        status = "✅" if left >= 0 else "⚠️"
        resp.message(
            f"✅ {desc} {fmt(amt)} — {user}\n\n"
            f"📅 Esta semana:\nGastado: {fmt(spent)}\n"
            f"{status} Queda: {fmt(left)}"
        )
        return Response(str(resp), mimetype="text/xml")

    resp.message('No entendi. Escribe "ayuda" para ver los comandos.')
    return Response(str(resp), mimetype="text/xml")

@app.route("/")
def index():
    return "Expense Agent running!"

with app.app_context():
    try:
        init_db()
    except:
        pass

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

