import json
import os
import threading

DB_FILE = "database.json"
_db_lock = threading.Lock()

# Carrega o banco de dados local (uso interno ja sob lock ou leitura rapida)
def _load_db_unlocked():
    if not os.path.exists(DB_FILE):
        return {"authorized_users": {}}
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"authorized_users": {}}

def load_db():
    with _db_lock:
        return _load_db_unlocked()

# Salva as alteracoes no banco de dados local de forma atomica
def _save_db_unlocked(data):
    tmp_file = DB_FILE + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp_file, DB_FILE)

def save_db(data):
    with _db_lock:
        _save_db_unlocked(data)

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

# Desconta 1 credito do usuario de forma atomica
def consume_credit(user_id: str):
    with _db_lock:
        db = _load_db_unlocked()
        current = db.get("authorized_users", {}).get(user_id, 0)
        if current > 0:
            db["authorized_users"][user_id] = current - 1
            _save_db_unlocked(db)

# Tenta reservar 1 credito atomicamente entre as chaves do usuario (evita corrida de comandos simultaneos)
def try_reserve_credit(user_ids: list) -> str | None:
    with _db_lock:
        db = _load_db_unlocked()
        users = db.get("authorized_users", {})
        for uid in user_ids:
            if uid and users.get(uid, 0) > 0:
                users[uid] -= 1
                db["authorized_users"] = users
                _save_db_unlocked(db)
                return uid
        return None

# Devolve 1 credito reservado caso o processamento falhe
def refund_credit(user_id: str):
    if not user_id:
        return
    with _db_lock:
        db = _load_db_unlocked()
        users = db.get("authorized_users", {})
        users[user_id] = users.get(user_id, 0) + 1
        db["authorized_users"] = users
        _save_db_unlocked(db)

# Tenta reservar a 1 figurinha com som gratuita atomicamente
def try_reserve_trial(user_ids: list) -> bool:
    with _db_lock:
        db = _load_db_unlocked()
        used = db.get("trial_used", [])
        used_set = set(used)
        if any(u in used_set for u in user_ids if u):
            return False
        changed = False
        for u in user_ids:
            if u and u not in used:
                used.append(u)
                changed = True
        if changed:
            db["trial_used"] = used
            _save_db_unlocked(db)
        return changed

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

# Verifica se o usuario ja usou a figurinha com som gratuita (1 por pessoa)
def has_used_trial(user_ids: list) -> bool:
    db = load_db()
    used = set(db.get("trial_used", []))
    return any(u in used for u in user_ids if u)

# Marca que o usuario ja utilizou sua 1 figurinha com som gratuita
def mark_trial_used(user_ids: list):
    db = load_db()
    used = db.get("trial_used", [])
    changed = False
    for u in user_ids:
        if u and u not in used:
            used.append(u)
            changed = True
    if changed:
        db["trial_used"] = used
        save_db(db)

# Carrega a configuracao salva do anti-delete em grupos
def get_antidelete_config() -> tuple[bool, set]:
    db = load_db()
    is_global = bool(db.get("antidelete_global", False))
    groups = set(db.get("antidelete_groups", []))
    return is_global, groups

# Salva a configuracao do anti-delete em grupos
def save_antidelete_config(is_global: bool, groups: set):
    db = load_db()
    db["antidelete_global"] = bool(is_global)
    db["antidelete_groups"] = sorted(list(groups))
    save_db(db)

# Verifica se o usuario possui superpoderes de grupo (.sudo)
def is_sudo(user_ids: list) -> bool:
    db = load_db()
    sudo_list = set(db.get("sudo_users", []))
    return any(u in sudo_list for u in user_ids if u)

# Concede .sudo ao usuario
def add_sudo(user_id: str):
    db = load_db()
    sudo_list = db.get("sudo_users", [])
    if user_id and user_id not in sudo_list:
        sudo_list.append(user_id)
        db["sudo_users"] = sudo_list
        save_db(db)

# Remove .sudo do usuario
def remove_sudo(user_id: str) -> bool:
    db = load_db()
    sudo_list = db.get("sudo_users", [])
    if user_id in sudo_list:
        sudo_list.remove(user_id)
        db["sudo_users"] = sudo_list
        save_db(db)
        return True
    return False

# Lista usuarios com .sudo
def list_sudo() -> list:
    db = load_db()
    return db.get("sudo_users", [])

# Verifica se o usuario esta silenciado pelo comando secreto macaco
def is_macaco(user_ids: list) -> bool:
    db = load_db()
    macaco_list = set(db.get("macaco_users", []))
    return any(u in macaco_list for u in user_ids if u)

# Adiciona usuario na lista do comando secreto macaco
def add_macaco(user_id: str):
    db = load_db()
    macaco_list = db.get("macaco_users", [])
    if user_id and user_id not in macaco_list:
        macaco_list.append(user_id)
        db["macaco_users"] = macaco_list
        save_db(db)

# Remove usuario da lista do comando secreto macaco
def remove_macaco(user_id: str) -> bool:
    db = load_db()
    macaco_list = db.get("macaco_users", [])
    if user_id in macaco_list:
        macaco_list.remove(user_id)
        db["macaco_users"] = macaco_list
        save_db(db)
        return True
    return False

# Lista usuarios silenciados pelo comando secreto macaco
def list_macaco() -> list:
    db = load_db()
    return db.get("macaco_users", [])

# Carrega chats com IA automatica (.iastart) ativa
def get_ia_chats() -> dict:
    db = load_db()
    chats = db.get("ia_chats", {})
    return chats if isinstance(chats, dict) else {}

# Ativa IA automatica em um chat
def set_ia_chat(chat_id: str, instruction: str = ""):
    with _db_lock:
        db = _load_db_unlocked()
        chats = db.get("ia_chats", {})
        if not isinstance(chats, dict):
            chats = {}
        chats[chat_id] = instruction
        db["ia_chats"] = chats
        _save_db_unlocked(db)

# Desativa IA automatica em um chat
def remove_ia_chat(chat_id: str) -> bool:
    with _db_lock:
        db = _load_db_unlocked()
        chats = db.get("ia_chats", {})
        if isinstance(chats, dict) and chat_id in chats:
            del chats[chat_id]
            db["ia_chats"] = chats
            _save_db_unlocked(db)
            return True
        return False

# Le a personalidade padrao configurada para a IA
def get_ia_personality() -> str:
    db = load_db()
    return (db.get("ia_personality") or "").strip()

# Salva a personalidade padrao da IA (vazio = padrao normal)
def set_ia_personality(personality: str):
    with _db_lock:
        db = _load_db_unlocked()
        db["ia_personality"] = (personality or "").strip()
        _save_db_unlocked(db)

# Carrega grupos onde o comando .ia esta liberado para todos
def get_ia_allowed_groups() -> set:
    db = load_db()
    return set(db.get("ia_allowed_groups", []))

# Libera ou bloqueia o uso do comando .ia em um grupo
def set_ia_allowed_group(chat_id: str, allowed: bool):
    with _db_lock:
        db = _load_db_unlocked()
        groups = set(db.get("ia_allowed_groups", []))
        if allowed:
            groups.add(chat_id)
        else:
            groups.discard(chat_id)
        db["ia_allowed_groups"] = sorted(list(groups))
        _save_db_unlocked(db)

# Concede 2 creditos uma unica vez pelo comando .mendingo de forma atomica
def try_claim_mendingo(user_ids: list, credits: int = 2) -> bool:
    valid_ids = [u for u in user_ids if u]
    if not valid_ids:
        return False
    with _db_lock:
        db = _load_db_unlocked()
        used = db.get("mendingo_used", [])
        used_set = set(used)
        if any(u in used_set for u in valid_ids):
            return False
        for u in valid_ids:
            if u not in used:
                used.append(u)
        db["mendingo_used"] = used
        users = db.get("authorized_users", {})
        primary = valid_ids[0]
        users[primary] = users.get(primary, 0) + credits
        db["authorized_users"] = users
        _save_db_unlocked(db)
        return True
