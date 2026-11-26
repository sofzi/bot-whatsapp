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

# Inicializa cliente do WhatsApp com sessao nova
client = NewClient("session.db")
owner_jid = None
owner_number = None

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
        print("Digite 'sair' no terminal ou .desligar no WhatsApp para fechar.")
        print("========================================\n")
    except Exception as e:
        print(f"Conectado. Erro ao ler dados: {e}")

# Evento de recebimento de mensagens
@client.event(MessageEv)
def on_message(c: NewClient, message: MessageEv):
    global owner_jid, owner_number

    msg_info = message.Info
    msg_id = msg_info.ID
    chat = msg_info.MessageSource.Chat
    sender = msg_info.MessageSource.Sender
    is_group = msg_info.MessageSource.IsGroup
    sender_number = sender.User

    raw_msg = message.Message

    # Desempacota se a mensagem veio do proprio celular (deviceSentMessage)
    if hasattr(raw_msg, "deviceSentMessage") and raw_msg.deviceSentMessage and raw_msg.deviceSentMessage.message:
        raw_msg = raw_msg.deviceSentMessage.message

    # Desempacota se a mensagem veio de grupo com mensagens temporarias
    if hasattr(raw_msg, "ephemeralMessage") and raw_msg.ephemeralMessage and raw_msg.ephemeralMessage.message:
        raw_msg = raw_msg.ephemeralMessage.message

    # Verifica se quem enviou foi o dono
    is_from_me = msg_info.MessageSource.IsFromMe or (owner_number and sender_number == owner_number)

    # 1. Anti-delete: captura evento de mensagem apagada (REVOKE = type 0)
    protocol_msg = getattr(raw_msg, "protocolMessage", None)
    if protocol_msg and protocol_msg.type == 0 and protocol_msg.key and protocol_msg.key.ID:
        revoked_id = protocol_msg.key.ID
        if revoked_id in message_cache:
            deleted_info = message_cache[revoked_id]
            author = deleted_info["sender_number"]
            where = deleted_info["chat_name"]
            content = deleted_info["text"]
            media_path = deleted_info.get("media_path")

            print(f"[Anti-Delete] Mensagem apagada por +{author} resgatada do cache.")

            alerta = (
                "Mensagem apagada detectada:\n"
                f"De: +{author}\n"
                f"Local: {where}\n"
                f"Horario: {deleted_info['time']}\n"
            )
            if content:
                alerta += f"Conteudo: {content}"

            dest = owner_jid if owner_jid else JIDToNonAD(chat)
            try:
                c.send_message(dest, alerta)

                if media_path and os.path.exists(media_path):
                    c.send_message(dest, f"Midia apagada de +{author}:")
                    if media_path.endswith((".jpg", ".png")):
                        c.send_image(dest, media_path)
                    elif media_path.endswith(".mp4"):
                        c.send_video(dest, media_path)
                    elif media_path.endswith((".ogg", ".mp3")):
                        c.send_audio(dest, media_path)
            except Exception as err:
                print(f"[Anti-Delete] Erro ao enviar notificacao: {err}")
        return

    # Ignora mensagens de protocolo que nao sejam apagamento
    if protocol_msg:
        return

    # Extrai o texto da mensagem
    texto_mensagem = (extract_text(raw_msg) or "").strip()
    chat_label = "Grupo" if is_group else f"Privado (+{sender_number})"

    # Log no terminal de mensagens recebidas
    if texto_mensagem:
        print(f"[Mensagem salva] De +{sender_number} em {chat_label}: {texto_mensagem[:40]}")

    # Salva copia de midia em temp caso seja apagada depois
    salved_media = None
    try:
        if raw_msg.imageMessage or raw_msg.videoMessage or raw_msg.audioMessage:
            temp_ext = ".jpg" if raw_msg.imageMessage else (".mp4" if raw_msg.videoMessage else ".ogg")
            salved_media = os.path.join(TEMP_DIR, f"{msg_id}{temp_ext}")
            c.download_any(raw_msg, salved_media)
            print(f"[Midia salva] De +{sender_number} em {chat_label}: {temp_ext}")
    except Exception:
        salved_media = None

    # Guarda mensagem no cache
    message_cache[msg_id] = {
        "sender_number": sender_number,
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

    # 2. Comandos de Administrador (Dono)
    if is_from_me:
        # Comando para desligar
        if texto_mensagem.lower() in [".encerrar", ".desligar", ".stop"]:
            print("Comando de encerramento recebido. Desligando...")
            try:
                c.reply_message("Encerrando bot...", message)
            except Exception:
                pass
            time.sleep(1)
            os._exit(0)

        # Comando .authorize
        if texto_mensagem.lower().startswith(".authorize"):
            partes = texto_mensagem.split()
            qtd = 2
            alvo = None

            ctx = getattr(raw_msg.extendedTextMessage, "contextInfo", None)
            if ctx and ctx.participant:
                alvo = ctx.participant.split("@")[0]
                if len(partes) > 1 and partes[1].isdigit():
                    qtd = int(partes[1])
            elif len(partes) > 1:
                alvo = partes[1].replace("+", "").replace("@", "").strip()
                if len(partes) > 2 and partes[2].isdigit():
                    qtd = int(partes[2])

            if alvo:
                db.add_authorization(alvo, qtd)
                c.reply_message(f"Usuario +{alvo} autorizado com {qtd} creditos.", message)
            else:
                c.reply_message("Uso: responda a mensagem com .authorize ou digite: .authorize <numero>", message)
            return

        # Comando .remover
        if texto_mensagem.lower().startswith(".remover"):
            partes = texto_mensagem.split()
            alvo = None
            ctx = getattr(raw_msg.extendedTextMessage, "contextInfo", None)
            if ctx and ctx.participant:
                alvo = ctx.participant.split("@")[0]
            elif len(partes) > 1:
                alvo = partes[1].replace("+", "").replace("@", "").strip()

            if alvo:
                db.remove_authorization(alvo)
                c.reply_message(f"Usuario +{alvo} removido.", message)
            else:
                c.reply_message("Uso: responda a mensagem com .remover ou digite: .remover <numero>", message)
            return

    # 3. Comando .audiosticker
    if ".audiosticker" in texto_mensagem.lower():
        # Bloqueio em grupos
        if is_group:
            c.reply_message("sai dai bixo", message)
            return

        # Bloqueio para nao autorizados no privado
        if not is_from_me and not db.is_authorized(sender_number):
            c.reply_message("aguarde o adm", message)
            return

        # Busca o video na mensagem ou na mensagem citada
        video_target = None
        if raw_msg.videoMessage:
            video_target = raw_msg
        else:
            ctx = getattr(raw_msg.extendedTextMessage, "contextInfo", None)
            if ctx and ctx.quotedMessage and ctx.quotedMessage.videoMessage:
                video_target = ctx.quotedMessage

        if not video_target:
            c.reply_message("Envie um video com .audiosticker na legenda ou responda a um video com o comando.", message)
            return

        input_video = os.path.join(TEMP_DIR, f"input_{msg_id}.mp4")
        output_webp = os.path.join(TEMP_DIR, f"sticker_{msg_id}.webp")
        output_audio = os.path.join(TEMP_DIR, f"audio_{msg_id}.ogg")

        try:
            c.download_any(video_target, input_video)
            sticker_ok = mp.convert_video_to_sticker(input_video, output_webp, max_duration=5)
            audio_ok = mp.extract_audio(input_video, output_audio, max_duration=5)

            if sticker_ok:
                c.send_sticker(chat, output_webp, name="audiosticker", packname="bot")
                if audio_ok:
                    c.send_audio(chat, output_audio, ptt=True)

                if not is_from_me:
                    db.consume_credit(sender_number)
            else:
                c.reply_message("Nao foi possivel converter o video.", message)

        except Exception as e:
            print(f"Erro ao processar audiosticker: {e}")
            c.reply_message("Erro ao processar o video.", message)
        finally:
            for temp_file in [input_video, output_webp, output_audio]:
                if os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except Exception:
                        pass

if __name__ == "__main__":
    print("Iniciando bot. Escaneie o QR Code que aparecera no terminal.")
    threading.Thread(target=escutar_terminal, daemon=True).start()
    client.connect()
