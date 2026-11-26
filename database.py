import json
import os

DB_FILE = "database.json"

# Carrega o banco de dados local
def load_db():
    if not os.path.exists(DB_FILE):
        return {"authorized_users": {}}
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"authorized_users": {}}

# Salva as alteracoes no banco de dados local
def save_db(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# Verifica se o usuario tem permissao e creditos restantes
def is_authorized(user_id: str) -> bool:
    db = load_db()
    credits = db.get("authorized_users", {}).get(user_id, 0)
    return credits > 0

# Retorna a quantidade de creditos de um usuario
def get_credits(user_id: str) -> int:
    db = load_db()
    return db.get("authorized_users", {}).get(user_id, 0)

# Adiciona creditos para o usuario (padrao 2)
def add_authorization(user_id: str, count: int = 2):
    db = load_db()
    if "authorized_users" not in db:
        db["authorized_users"] = {}
    db["authorized_users"][user_id] = count
    save_db(db)
    return count

# Desconta 1 credito do usuario
def consume_credit(user_id: str):
    db = load_db()
    current = db.get("authorized_users", {}).get(user_id, 0)
    if current > 0:
        db["authorized_users"][user_id] = current - 1
        save_db(db)

# Remove totalmente a autorizacao do usuario
def remove_authorization(user_id: str):
    db = load_db()
    if user_id in db.get("authorized_users", {}):
        del db["authorized_users"][user_id]
        save_db(db)
        return True
    return False

# Lista todos os usuarios autorizados com creditos ativos
def list_authorized() -> dict:
    db = load_db()
    users = db.get("authorized_users", {})
    return {u: c for u, c in users.items() if c > 0}

# Verifica se um usuario esta bloqueado (ignorado em silencio)
def is_blocked(user_id: str) -> bool:
    db = load_db()
    return user_id in db.get("blocked_users", [])

# Adiciona usuario na lista de bloqueados
def block_user(user_id: str):
    db = load_db()
    blocked = db.get("blocked_users", [])
    if user_id not in blocked:
        blocked.append(user_id)
        db["blocked_users"] = blocked
        save_db(db)

# Remove usuario da lista de bloqueados
def unblock_user(user_id: str) -> bool:
    db = load_db()
    blocked = db.get("blocked_users", [])
    if user_id in blocked:
        blocked.remove(user_id)
        db["blocked_users"] = blocked
        save_db(db)
        return True
    return False

# Lista todos os usuarios bloqueados
def list_blocked() -> list:
    db = load_db()
    return db.get("blocked_users", [])
