import os
import sys
import time
import glob
import shutil
import threading
from datetime import datetime
import imageio_ffmpeg

# Apaga a sessao anterior para sempre exigir um novo QR Code ao abrir
for f in glob.glob("session.db*"):
    try:
        os.remove(f)
    except Exception:
        pass

# Configura logger duplo para terminal e bot.log
class Logger(object):
    def __init__(self):
        self.terminal = sys.stdout
        self.log = open("bot.log", "a", encoding="utf-8")

    def write(self, message):
        try:
            self.terminal.write(message)
            self.terminal.flush()
        except Exception:
            pass
        try:
            self.log.write(message)
            self.log.flush()
        except Exception:
            pass

    def flush(self):
        try:
            self.terminal.flush()
            self.log.flush()
        except Exception:
            pass

sys.stdout = Logger()
sys.stderr = Logger()

# Configura FFmpeg e FFprobe locais no PATH
os.makedirs("bin", exist_ok=True)
_ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
_local_ffmpeg = os.path.join("bin", "ffmpeg.exe")
_local_ffprobe = os.path.join("bin", "ffprobe.exe")
if not os.path.exists(_local_ffmpeg):
    try:
        shutil.copyfile(_ffmpeg_exe, _local_ffmpeg)
    except Exception:
        pass
if not os.path.exists(_local_ffprobe):
    try:
        shutil.copyfile(_ffmpeg_exe, _local_ffprobe)
    except Exception:
        pass
os.environ["PATH"] = os.path.abspath("bin") + os.pathsep + os.environ.get("PATH", "")

from neonize.client import NewClient
from neonize.events import ConnectedEv, MessageEv
from neonize.utils import extract_text
from neonize.utils.jid import build_jid, JIDToNonAD
import database as db
import media_processor as mp

TEMP_DIR = "temp"
os.makedirs(TEMP_DIR, exist_ok=True)

# Cache de mensagens para anti-delete
message_cache = {}

# Controle do anti-delete em grupos (desativado por padrao para nao sobrecarregar)
antidelete_grupos_global = False
antidelete_grupos_ativos = set()

# Inicializa cliente do WhatsApp com sessao nova
client = NewClient("session.db")
owner_jid = None
owner_number = None

# Verifica com seguranca se um sub-campo Protobuf realmente existe na mensagem
def has_field(obj, field_name: str) -> bool:
    try:
        return obj is not None and obj.HasField(field_name)
    except Exception:
        return False

# Desempacota camadas extras da mensagem apenas quando realmente existem
def desempacotar_mensagem(msg):
    if not msg:
        return msg
    for _ in range(5):
        if has_field(msg, "deviceSentMessage") and has_field(msg.deviceSentMessage, "message"):
            msg = msg.deviceSentMessage.message
            continue
        if has_field(msg, "ephemeralMessage") and has_field(msg.ephemeralMessage, "message"):
            msg = msg.ephemeralMessage.message
            continue
        if has_field(msg, "viewOnceMessage") and has_field(msg.viewOnceMessage, "message"):
            msg = msg.viewOnceMessage.message
            continue
        if has_field(msg, "viewOnceMessageV2") and has_field(msg.viewOnceMessageV2, "message"):
            msg = msg.viewOnceMessageV2.message
            continue
        if has_field(msg, "documentWithCaptionMessage") and has_field(msg.documentWithCaptionMessage, "message"):
            msg = msg.documentWithCaptionMessage.message
            continue
        break
    return msg

# Thread para leitura de comandos no terminal
def escutar_terminal():
    while True:
        try:
            linha = input().strip().lower()
            if linha in ["sair", "exit", "stop", "encerrar", "desligar"]:
                print("Encerrando bot pelo terminal...")
                os._exit(0)
        except (EOFError, KeyboardInterrupt):
            break

# Evento de conexao bem sucedida
@client.event(ConnectedEv)
def on_connected(c: NewClient, _: ConnectedEv):
    global owner_jid, owner_number
    try:
        me = c.get_me()
        owner_number = me.JID.User
        owner_jid = build_jid(owner_number, "s.whatsapp.net")
        print("\n========================================")
        print(f"Conectado como: {me.PushName} ({owner_number})")
        print("Anti-delete: Ativo no privado | Desativado em grupos (use .antidelete para ativar)")
        print("Digite 'sair' no terminal ou .desligar no WhatsApp para fechar.")
        print("========================================\n")
    except Exception as e:
        print(f"Conectado. Erro ao ler dados: {e}")

# Evento de recebimento de mensagens
@client.event(MessageEv)
def on_message(c: NewClient, message: MessageEv):
    global owner_jid, owner_number, antidelete_grupos_global

    msg_info = message.Info
    msg_id = msg_info.ID
    src = msg_info.MessageSource
    chat = src.Chat
    chat_clean = JIDToNonAD(chat)
    sender = src.Sender
    is_group = src.IsGroup

    # Extrai o numero real do remetente (mesmo quando o WhatsApp envia LID)
    sender_number = sender.User
    if sender.Server == "lid" and has_field(src, "SenderAlt") and src.SenderAlt.User:
        sender_number = src.SenderAlt.User

    # Verifica se quem enviou foi o dono
    is_device_sent = has_field(message.Message, "deviceSentMessage")
    is_from_me = src.IsFromMe or is_device_sent or (owner_number and sender_number == owner_number)

    raw_msg = desempacotar_mensagem(message.Message)

    # Verifica se o anti-delete deve monitorar esta conversa
    monitorar_conversa = (not is_group) or antidelete_grupos_global or (chat.User in antidelete_grupos_ativos)

    # 1. Anti-delete: verifica se a mensagem realmente possui um protocolMessage
    if has_field(raw_msg, "protocolMessage"):
        if not monitorar_conversa:
            return

        protocol_msg = raw_msg.protocolMessage
        if protocol_msg.type == 0 and has_field(protocol_msg, "key") and protocol_msg.key.ID:
            revoked_id = protocol_msg.key.ID
            if revoked_id in message_cache:
                deleted_info = message_cache[revoked_id]
                author = deleted_info["sender_number"]
                pushname = deleted_info.get("pushname", "")
                where = deleted_info["chat_name"]
                content = deleted_info["text"]
                media_path = deleted_info.get("media_path")

                autor_label = f"{pushname} (+{author})" if pushname else f"+{author}"
                print(f"[Anti-Delete] Mensagem apagada por {autor_label} em {where} resgatada do cache.")

                alerta = (
                    "Mensagem apagada detectada:\n"
                    f"De: {autor_label}\n"
                    f"Local: {where}\n"
                    f"Horario: {deleted_info['time']}\n"
                )
                if content:
                    alerta += f"Conteudo: {content}"

                dest = owner_jid if owner_jid else chat_clean
                try:
                    c.send_message(dest, alerta)

                    if media_path and os.path.exists(media_path):
                        c.send_message(dest, f"Midia apagada de {autor_label}:")
                        if media_path.endswith((".jpg", ".png")):
                            c.send_image(dest, media_path)
                        elif media_path.endswith(".mp4"):
                            c.send_video(dest, media_path)
                        elif media_path.endswith((".ogg", ".mp3")):
                            c.send_audio(dest, media_path)
                except Exception as err:
                    print(f"[Anti-Delete] Erro ao enviar notificacao: {err}")
            else:
                print(f"[Anti-Delete] Mensagem apagada (ID {revoked_id}), mas nao estava no cache.")
        return

    # Extrai o texto da mensagem
    texto_mensagem = (extract_text(raw_msg) or "").strip()
    texto_lower = texto_mensagem.lower()

    # 2. Comandos de Administrador (Dono)
    if is_from_me:
        # Comando .antidelete (ativa leitura de grupos)
        if texto_lower == ".antidelete":
            if is_group:
                antidelete_grupos_ativos.add(chat.User)
                print(f"[Anti-Delete] Ativado para o grupo {chat.User}")
                c.reply_message("Anti-delete ativado neste grupo.", message, to=chat_clean)
            else:
                antidelete_grupos_global = True
                print("[Anti-Delete] Ativado para todos os grupos.")
                c.reply_message("Anti-delete ativado para todos os grupos.", message, to=chat_clean)
            return

        # Comando .stopantidelete (para leitura de grupos)
        if texto_lower == ".stopantidelete":
            if is_group:
                antidelete_grupos_ativos.discard(chat.User)
                print(f"[Anti-Delete] Desativado para o grupo {chat.User}")
                c.reply_message("Anti-delete desativado neste grupo.", message, to=chat_clean)
            else:
                antidelete_grupos_global = False
                antidelete_grupos_ativos.clear()
                print("[Anti-Delete] Desativado em todos os grupos.")
                c.reply_message("Anti-delete desativado em todos os grupos.", message, to=chat_clean)
            return

        # Comando para desligar
        if texto_lower in [".encerrar", ".desligar", ".stop"]:
            print("Comando de encerramento recebido pelo WhatsApp. Desligando...")
            try:
                c.reply_message("Encerrando bot...", message, to=chat_clean)
            except Exception:
                pass
            time.sleep(1)
            os._exit(0)

        # Comando .authorize
        if texto_lower.startswith(".authorize"):
            partes = texto_mensagem.split()
            qtd = 2
            alvo = None

            if has_field(raw_msg, "extendedTextMessage") and has_field(raw_msg.extendedTextMessage, "contextInfo"):
                ctx = raw_msg.extendedTextMessage.contextInfo
                if ctx.participant:
                    alvo = ctx.participant.split("@")[0]
                    if len(partes) > 1 and partes[1].isdigit():
                        qtd = int(partes[1])

            if not alvo and len(partes) > 1:
                alvo = partes[1].replace("+", "").replace("@", "").strip()
                if len(partes) > 2 and partes[2].isdigit():
                    qtd = int(partes[2])

            if not alvo and not is_group and chat.User != owner_number:
                alvo = chat.User

            if alvo:
                db.add_authorization(alvo, qtd)
                c.reply_message(f"Usuario +{alvo} autorizado com {qtd} creditos.", message, to=chat_clean)
            else:
                c.reply_message("Uso: responda a mensagem com .authorize ou digite: .authorize <numero>", message, to=chat_clean)
            return

        # Comando .remover
        if texto_lower.startswith(".remover"):
            partes = texto_mensagem.split()
            alvo = None

            if has_field(raw_msg, "extendedTextMessage") and has_field(raw_msg.extendedTextMessage, "contextInfo"):
                ctx = raw_msg.extendedTextMessage.contextInfo
                if ctx.participant:
                    alvo = ctx.participant.split("@")[0]

            if not alvo and len(partes) > 1:
                alvo = partes[1].replace("+", "").replace("@", "").strip()

            if not alvo and not is_group and chat.User != owner_number:
                alvo = chat.User

            if alvo:
                db.remove_authorization(alvo)
                c.reply_message(f"Usuario +{alvo} removido.", message, to=chat_clean)
            else:
                c.reply_message("Uso: responda a mensagem com .remover ou digite: .remover <numero>", message, to=chat_clean)
            return

    # 3. Comando .ping
    if texto_lower == ".ping":
        agora_ms = int(time.time() * 1000)
        msg_ts = msg_info.Timestamp
        if msg_ts < 100000000000:
            msg_ts *= 1000
        ping_ms = agora_ms - msg_ts
        if ping_ms <= 0 or ping_ms > 10000:
            t0 = time.perf_counter()
            c.get_me()
            ping_ms = max(1, int((time.perf_counter() - t0) * 1000))
        c.reply_message(f"Pong! {ping_ms}ms", message, to=chat_clean)
        return

    # 4. Comando .audiosticker
    if ".audiosticker" in texto_lower:
        # Bloqueio em grupos
        if is_group:
            c.reply_message("sai dai bixo", message, to=chat_clean)
            return

        # Bloqueio para nao autorizados no privado
        if not is_from_me and not (db.is_authorized(sender_number) or db.is_authorized(chat.User)):
            c.reply_message("aguarde o adm", message, to=chat_clean)
            return

        # Busca o video na propria mensagem ou na mensagem respondida
        video_target = None
        if has_field(raw_msg, "videoMessage"):
            video_target = raw_msg
        elif has_field(raw_msg, "extendedTextMessage") and has_field(raw_msg.extendedTextMessage, "contextInfo"):
            ctx = raw_msg.extendedTextMessage.contextInfo
            if has_field(ctx, "quotedMessage"):
                quoted_unwrapped = desempacotar_mensagem(ctx.quotedMessage)
                if has_field(quoted_unwrapped, "videoMessage"):
                    video_target = quoted_unwrapped

        if not video_target:
            c.reply_message("Envie um video com .audiosticker na legenda ou responda a um video com o comando.", message, to=chat_clean)
            return

        input_video = os.path.join(TEMP_DIR, f"input_{msg_id}.mp4")
        output_webp = os.path.join(TEMP_DIR, f"sticker_{msg_id}.webp")
        output_audio = os.path.join(TEMP_DIR, f"audio_{msg_id}.ogg")

        try:
            c.download_any(video_target, input_video)
            sticker_ok = mp.convert_video_to_sticker(input_video, output_webp, max_duration=5)
            audio_ok = mp.extract_audio(input_video, output_audio, max_duration=5)

            if sticker_ok:
                c.send_sticker(chat_clean, output_webp, name="audiosticker", packname="bot")
                if audio_ok:
                    c.send_audio(chat_clean, output_audio, ptt=True)

                if not is_from_me:
                    if db.is_authorized(sender_number):
                        db.consume_credit(sender_number)
                    elif db.is_authorized(chat.User):
                        db.consume_credit(chat.User)
            else:
                c.reply_message("Nao foi possivel converter o video.", message, to=chat_clean)

        except Exception as e:
            print(f"Erro ao processar audiosticker: {e}")
            c.reply_message("Erro ao processar o video.", message, to=chat_clean)
        finally:
            for temp_file in [input_video, output_webp, output_audio]:
                if os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except Exception:
                        pass
        return

    # 5. Cache do Anti-Delete: ignora grupos se o monitoramento nao estiver ativo
    if not monitorar_conversa:
        return

    chat_label = f"Privado (+{sender_number})"
    if is_group:
        chat_label = f"Grupo ({chat.User})"
        try:
            g_info = c.get_group_info(chat)
            if g_info and g_info.GroupName and g_info.GroupName.Name:
                chat_label = f"Grupo {g_info.GroupName.Name}"
        except Exception:
            pass

    if texto_mensagem:
        print(f"[Mensagem] De +{sender_number} em {chat_label}: {texto_mensagem[:50]}")

    # Salva copia de midia em temp apenas nas conversas monitoradas
    salved_media = None
    try:
        if has_field(raw_msg, "imageMessage"):
            salved_media = os.path.join(TEMP_DIR, f"{msg_id}.jpg")
            c.download_any(raw_msg, salved_media)
            print(f"[Midia salva] Imagem de +{sender_number} em {chat_label}")
        elif has_field(raw_msg, "videoMessage"):
            salved_media = os.path.join(TEMP_DIR, f"{msg_id}.mp4")
            c.download_any(raw_msg, salved_media)
            print(f"[Midia salva] Video de +{sender_number} em {chat_label}")
        elif has_field(raw_msg, "audioMessage"):
            salved_media = os.path.join(TEMP_DIR, f"{msg_id}.ogg")
            c.download_any(raw_msg, salved_media)
            print(f"[Midia salva] Audio de +{sender_number} em {chat_label}")
    except Exception:
        salved_media = None

    if texto_mensagem or salved_media:
        message_cache[msg_id] = {
            "sender_number": sender_number,
            "pushname": msg_info.Pushname or "",
            "chat_name": chat_label,
            "text": texto_mensagem,
            "media_path": salved_media,
            "time": datetime.now().strftime("%H:%M:%S")
        }

    # Mantem o cache com no maximo 1000 mensagens
    if len(message_cache) > 1000:
        oldest_key = next(iter(message_cache))
        old_data = message_cache.pop(oldest_key)
        if old_data.get("media_path") and os.path.exists(old_data["media_path"]):
            try:
                os.remove(old_data["media_path"])
            except Exception:
                pass

if __name__ == "__main__":
    print("Iniciando bot. Escaneie o QR Code que aparecera no terminal.")
    threading.Thread(target=escutar_terminal, daemon=True).start()
    client.connect()
