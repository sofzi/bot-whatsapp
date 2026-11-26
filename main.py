import os
import re
import sys
import time
import glob
import shutil
import subprocess
import threading
from datetime import datetime

# Codigo de saida especial para indicar reinicializacao mantendo a sessao
RESTART_EXIT_CODE = 42
KEEP_SESSION_FLAG = ".keep_session"

# Remove todos os arquivos de sessao do disco
def limpar_arquivos_sessao():
    if os.path.exists(KEEP_SESSION_FLAG):
        try:
            os.remove(KEEP_SESSION_FLAG)
        except Exception:
            pass
    for f in glob.glob("session.db*"):
        try:
            os.remove(f)
        except Exception:
            pass

# Se executado diretamente pelo usuario, roda como supervisor para suportar .restart
if __name__ == "__main__" and "--worker" not in sys.argv:
    # Ao iniciar do zero, garante que qualquer sessao antiga seja apagada
    limpar_arquivos_sessao()
    while True:
        try:
            ret = subprocess.call([sys.executable, __file__, "--worker"])
        except KeyboardInterrupt:
            limpar_arquivos_sessao()
            break

        if ret == RESTART_EXIT_CODE:
            print("\nReiniciando bot e mantendo a sessao atual...\n")
            time.sleep(1)
            continue

        # Se encerrou normalmente (.desligar ou sair), apaga a sessao
        limpar_arquivos_sessao()
        break
    sys.exit(0)

import imageio_ffmpeg

# No processo worker: apaga a sessao apenas se nao veio de um .restart
if os.path.exists(KEEP_SESSION_FLAG):
    try:
        os.remove(KEEP_SESSION_FLAG)
    except Exception:
        pass
else:
    limpar_arquivos_sessao()

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

# Cache de mensagens para anti-delete e anti-edit
message_cache = {}

# Controle do monitoramento em grupos (desativado por padrao)
antidelete_grupos_global = False
antidelete_grupos_ativos = set()

# Inicializa cliente do WhatsApp
client = NewClient("session.db")
owner_jid = None
owner_number = None

# Encerra o bot apagando a sessao
def encerrar_perdendo_sessao():
    try:
        client.logout()
    except Exception:
        pass
    try:
        client.disconnect()
    except Exception:
        pass
    limpar_arquivos_sessao()
    os._exit(0)

# Reinicia o bot preservando a sessao atual
def reiniciar_mantendo_sessao():
    try:
        with open(KEEP_SESSION_FLAG, "w", encoding="utf-8") as f:
            f.write("1")
    except Exception:
        pass
    try:
        client.disconnect()
    except Exception:
        pass
    os._exit(RESTART_EXIT_CODE)

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
    for _ in range(6):
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
        if has_field(msg, "viewOnceMessageV2Extension") and has_field(msg.viewOnceMessageV2Extension, "message"):
            msg = msg.viewOnceMessageV2Extension.message
            continue
        if has_field(msg, "documentWithCaptionMessage") and has_field(msg.documentWithCaptionMessage, "message"):
            msg = msg.documentWithCaptionMessage.message
            continue
        if has_field(msg, "editedMessage") and has_field(msg.editedMessage, "message"):
            msg = msg.editedMessage.message
            continue
        break
    return msg

# Verifica se a mensagem original e de visualizacao unica
def is_view_once(orig_msg, unwrapped_msg) -> bool:
    cur = orig_msg
    for _ in range(5):
        if not cur:
            break
        if (
            has_field(cur, "viewOnceMessage")
            or has_field(cur, "viewOnceMessageV2")
            or has_field(cur, "viewOnceMessageV2Extension")
        ):
            return True
        if has_field(cur, "deviceSentMessage") and has_field(cur.deviceSentMessage, "message"):
            cur = cur.deviceSentMessage.message
            continue
        if has_field(cur, "ephemeralMessage") and has_field(cur.ephemeralMessage, "message"):
            cur = cur.ephemeralMessage.message
            continue
        break

    if has_field(unwrapped_msg, "imageMessage") and unwrapped_msg.imageMessage.viewOnce:
        return True
    if has_field(unwrapped_msg, "videoMessage") and unwrapped_msg.videoMessage.viewOnce:
        return True
    if has_field(unwrapped_msg, "audioMessage") and unwrapped_msg.audioMessage.viewOnce:
        return True
    return False

# Extrai o primeiro link (http/https) de um texto
def extrair_url(texto: str) -> str | None:
    match = re.search(r"(https?://[^\s]+)", texto or "")
    return match.group(1) if match else None

# Retorna a mensagem citada (quotedMessage) desempacotada, se houver
def obter_mensagem_citada(raw_msg):
    if has_field(raw_msg, "extendedTextMessage") and has_field(raw_msg.extendedTextMessage, "contextInfo"):
        ctx = raw_msg.extendedTextMessage.contextInfo
        if has_field(ctx, "quotedMessage"):
            return desempacotar_mensagem(ctx.quotedMessage), ctx
    return None, None

# Thread para leitura de comandos no terminal
def escutar_terminal():
    while True:
        try:
            linha = input().strip().lower()
            if linha in ["sair", "exit", "stop", "encerrar", "desligar"]:
                print("Encerrando bot e limpando sessao...")
                encerrar_perdendo_sessao()
            if linha in ["restart", "reiniciar", ".restart"]:
                print("Reiniciando bot pelo terminal (mantendo sessao)...")
                reiniciar_mantendo_sessao()
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
        print("Monitoramento (Anti-delete / Anti-edit / View-once): Ativo no privado | Use .antidelete para grupos")
        print("Terminal: digite 'restart' para reiniciar ou 'sair' para desligar e apagar sessao.")
        print("WhatsApp: envie .restart para reiniciar ou .desligar para fechar e apagar sessao.")
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
    sender = src.Sender

    # Ignora canais (newsletters) e status/transmissoes (broadcast)
    if chat.Server in ["newsletter", "broadcast"] or sender.Server in ["newsletter", "broadcast"]:
        return

    chat_clean = JIDToNonAD(chat)
    is_group = src.IsGroup

    # Extrai o numero real do remetente (mesmo quando o WhatsApp envia LID)
    sender_number = sender.User
    if sender.Server == "lid" and has_field(src, "SenderAlt") and src.SenderAlt.User:
        sender_number = src.SenderAlt.User

    # Verificacao estrita de dono: apenas mensagens autenticadas como IsFromMe pelo protocolo ou do proprio JID do dono
    is_from_me = bool(src.IsFromMe) or bool(
        owner_number and sender_number == owner_number and sender.Server == "s.whatsapp.net"
    )

    raw_msg = desempacotar_mensagem(message.Message)

    # Verifica se o monitoramento deve observar esta conversa
    monitorar_conversa = (not is_group) or antidelete_grupos_global or (chat.User in antidelete_grupos_ativos)

    # 1. Anti-delete (type == 0) e Anti-edit (type == 14)
    if has_field(raw_msg, "protocolMessage"):
        if not monitorar_conversa:
            return

        protocol_msg = raw_msg.protocolMessage
        dest = owner_jid if owner_jid else chat_clean

        # Caso 1A: Mensagem apagada (REVOKE = 0)
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

        # Caso 1B: Mensagem editada (MESSAGE_EDIT = 14)
        if protocol_msg.type == 14 and has_field(protocol_msg, "key") and protocol_msg.key.ID:
            edited_id = protocol_msg.key.ID
            novo_texto = ""
            if has_field(protocol_msg, "editedMessage"):
                novo_texto = (extract_text(desempacotar_mensagem(protocol_msg.editedMessage)) or "").strip()

            if edited_id in message_cache and not is_from_me:
                orig_info = message_cache[edited_id]
                texto_antigo = orig_info.get("text", "")
                author = orig_info["sender_number"]
                pushname = orig_info.get("pushname", "")
                where = orig_info["chat_name"]

                if texto_antigo != novo_texto:
                    autor_label = f"{pushname} (+{author})" if pushname else f"+{author}"
                    print(f"[Anti-Edit] Mensagem editada por {autor_label} em {where}: '{texto_antigo}' -> '{novo_texto}'")

                    alerta_edit = (
                        "Mensagem editada detectada:\n"
                        f"De: {autor_label}\n"
                        f"Local: {where}\n"
                        f"Horario: {datetime.now().strftime('%H:%M:%S')}\n"
                        f"Antes: {texto_antigo}\n"
                        f"Depois: {novo_texto}"
                    )
                    try:
                        c.send_message(dest, alerta_edit)
                    except Exception as err:
                        print(f"[Anti-Edit] Erro ao enviar notificacao: {err}")

                    message_cache[edited_id]["text"] = novo_texto
            return

        return

    # Extrai o texto da mensagem
    texto_mensagem = (extract_text(raw_msg) or "").strip()
    texto_lower = texto_mensagem.lower()

    # Lista de comandos exclusivos do dono
    comandos_adm = (
        ".antidelete",
        ".stopantidelete",
        ".ver",
        ".lista",
        ".encerrar",
        ".desligar",
        ".stop",
        ".restart",
        ".reiniciar",
        ".authorize",
        ".remover",
    )

    # Se outra pessoa tentar usar qualquer comando de admin, bloqueia e ignora
    if not is_from_me and any(texto_lower == cmd or texto_lower.startswith(cmd + " ") for cmd in comandos_adm):
        print(f"[Seguranca] Tentativa bloqueada de comando admin por +{sender_number}: {texto_mensagem}")
        return

    # 2. Comandos de Administrador (Exclusivos do Dono)
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

        # Comando .ver (resgata visualizacao unica ou midia marcada sob demanda para o privado do dono)
        if texto_lower == ".ver":
            quoted_msg, _ = obter_mensagem_citada(raw_msg)
            if not quoted_msg:
                c.reply_message("Responda a uma foto, video ou audio de visualizacao unica com .ver", message, to=chat_clean)
                return

            ext = None
            if has_field(quoted_msg, "imageMessage"):
                ext = ".jpg"
            elif has_field(quoted_msg, "videoMessage"):
                ext = ".mp4"
            elif has_field(quoted_msg, "audioMessage"):
                ext = ".ogg"

            if not ext:
                c.reply_message("A mensagem respondida nao contem foto, video ou audio.", message, to=chat_clean)
                return

            temp_ver = os.path.join(TEMP_DIR, f"ver_{msg_id}{ext}")
            dest = owner_jid if owner_jid else chat_clean
            try:
                c.download_any(quoted_msg, temp_ver)
                if os.path.exists(temp_ver):
                    c.send_message(dest, "Midia resgatada com .ver:")
                    if ext == ".jpg":
                        c.send_image(dest, temp_ver)
                    elif ext == ".mp4":
                        c.send_video(dest, temp_ver)
                    elif ext == ".ogg":
                        c.send_audio(dest, temp_ver)
            except Exception as e:
                print(f"Erro no comando .ver: {e}")
                c.reply_message("Nao foi possivel baixar a midia marcada.", message, to=chat_clean)
            finally:
                if os.path.exists(temp_ver):
                    try:
                        os.remove(temp_ver)
                    except Exception:
                        pass
            return

        # Comando .lista (lista todos os usuarios autorizados e seus creditos)
        if texto_lower == ".lista":
            autorizados = db.list_authorized()
            if not autorizados:
                c.reply_message("Nenhum usuario autorizado no momento.", message, to=chat_clean)
            else:
                linhas = ["Usuarios autorizados:"]
                for num, cred in autorizados.items():
                    linhas.append(f"- +{num}: {cred} credito(s)")
                c.reply_message("\n".join(linhas), message, to=chat_clean)
            return

        # Comando .restart (reinicia o bot mantendo a sessao atual)
        if texto_lower in [".restart", ".reiniciar"]:
            print("Comando .restart recebido pelo WhatsApp. Reiniciando sem perder a sessao...")
            try:
                c.reply_message("Reiniciando bot (mantendo sessao)...", message, to=chat_clean)
            except Exception:
                pass
            time.sleep(1)
            reiniciar_mantendo_sessao()

        # Comando .desligar (encerra o bot e apaga a sessao)
        if texto_lower in [".encerrar", ".desligar", ".stop"]:
            print("Comando .desligar recebido pelo WhatsApp. Apagando sessao e desligando...")
            try:
                c.reply_message("Desligando bot e encerrando sessao...", message, to=chat_clean)
            except Exception:
                pass
            time.sleep(1)
            encerrar_perdendo_sessao()

        # Comando .authorize
        if texto_lower.startswith(".authorize"):
            partes = texto_mensagem.split()
            qtd = 2
            alvos = set()

            if has_field(raw_msg, "extendedTextMessage") and has_field(raw_msg.extendedTextMessage, "contextInfo"):
                ctx = raw_msg.extendedTextMessage.contextInfo
                if ctx.participant:
                    alvos.add(ctx.participant.split("@")[0].split(":")[0])
                if ctx.stanzaID and ctx.stanzaID in message_cache:
                    alvos.add(message_cache[ctx.stanzaID]["sender_number"])
                if len(partes) > 1 and partes[1].isdigit():
                    qtd = int(partes[1])

            if not alvos and len(partes) > 1:
                alvos.add(partes[1].replace("+", "").replace("@", "").strip())
                if len(partes) > 2 and partes[2].isdigit():
                    qtd = int(partes[2])

            if not alvos and not is_group and chat.User != owner_number:
                alvos.add(chat.User)

            if alvos:
                for a in alvos:
                    db.add_authorization(a, qtd)
                principal = next(iter(alvos))
                c.reply_message(f"Usuario +{principal} autorizado com {qtd} creditos.", message, to=chat_clean)
            else:
                c.reply_message("Uso: responda a mensagem com .authorize ou digite: .authorize <numero>", message, to=chat_clean)
            return

        # Comando .remover
        if texto_lower.startswith(".remover"):
            partes = texto_mensagem.split()
            alvos = set()

            if has_field(raw_msg, "extendedTextMessage") and has_field(raw_msg.extendedTextMessage, "contextInfo"):
                ctx = raw_msg.extendedTextMessage.contextInfo
                if ctx.participant:
                    alvos.add(ctx.participant.split("@")[0].split(":")[0])
                if ctx.stanzaID and ctx.stanzaID in message_cache:
                    alvos.add(message_cache[ctx.stanzaID]["sender_number"])

            if not alvos and len(partes) > 1:
                alvos.add(partes[1].replace("+", "").replace("@", "").strip())

            if not alvos and not is_group and chat.User != owner_number:
                alvos.add(chat.User)

            if alvos:
                for a in alvos:
                    db.remove_authorization(a)
                principal = next(iter(alvos))
                c.reply_message(f"Usuario +{principal} removido.", message, to=chat_clean)
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

    # Verifica chaves de autorizacao do usuario para os comandos de midia
    chaves_usuario = [sender_number, sender.User]
    if not is_group:
        chaves_usuario.append(chat.User)
    autorizado = is_from_me or any(db.is_authorized(k) for k in chaves_usuario)

    def bloquear_nao_autorizado():
        if is_group:
            c.reply_message("sai dai bixo", message, to=chat_clean)
        else:
            c.reply_message("aguarde o adm", message, to=chat_clean)

    def descontar_credito_usuario():
        if not is_from_me:
            for k in chaves_usuario:
                if db.is_authorized(k):
                    db.consume_credit(k)

    # 4. Comando .audiosticker (video anexado, video respondido ou link de rede social)
    if ".audiosticker" in texto_lower:
        if not autorizado:
            bloquear_nao_autorizado()
            return

        quoted_msg, _ = obter_mensagem_citada(raw_msg)
        video_target = None
        url_alvo = extrair_url(texto_mensagem)

        if has_field(raw_msg, "videoMessage"):
            video_target = raw_msg
        elif quoted_msg and has_field(quoted_msg, "videoMessage"):
            video_target = quoted_msg
        elif not url_alvo and quoted_msg:
            url_alvo = extrair_url(extract_text(quoted_msg))

        if not video_target and not url_alvo:
            c.reply_message("Envie um video, responda a um video ou cole um link junto com .audiosticker", message, to=chat_clean)
            return

        input_video = os.path.join(TEMP_DIR, f"input_{msg_id}.mp4")
        output_webp = os.path.join(TEMP_DIR, f"sticker_{msg_id}.webp")
        output_audio = os.path.join(TEMP_DIR, f"audio_{msg_id}.ogg")

        try:
            if video_target:
                c.download_any(video_target, input_video)
            else:
                mp.download_social_video(url_alvo, input_video)

            if not os.path.exists(input_video):
                c.reply_message("Nao foi possivel obter o video.", message, to=chat_clean)
                return

            sticker_ok = mp.convert_video_to_sticker(input_video, output_webp, max_duration=5)
            audio_ok = mp.extract_audio(input_video, output_audio, max_duration=5)

            if sticker_ok:
                c.send_sticker(chat_clean, output_webp, name="audiosticker", packname="bot")
                if audio_ok:
                    c.send_audio(chat_clean, output_audio, ptt=True)
                descontar_credito_usuario()
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

    # 5. Comando .fig (figurinha estatica ou animada sem audio separado)
    if texto_lower == ".fig" or texto_lower.startswith(".fig "):
        if not autorizado:
            bloquear_nao_autorizado()
            return

        quoted_msg, _ = obter_mensagem_citada(raw_msg)
        media_target = None
        is_video = False

        if has_field(raw_msg, "imageMessage"):
            media_target = raw_msg
        elif has_field(raw_msg, "videoMessage"):
            media_target = raw_msg
            is_video = True
        elif quoted_msg:
            if has_field(quoted_msg, "imageMessage"):
                media_target = quoted_msg
            elif has_field(quoted_msg, "videoMessage"):
                media_target = quoted_msg
                is_video = True

        if not media_target:
            c.reply_message("Envie uma foto/video com .fig na legenda ou responda a uma foto/video com .fig", message, to=chat_clean)
            return

        ext_in = ".mp4" if is_video else ".jpg"
        input_path = os.path.join(TEMP_DIR, f"fig_in_{msg_id}{ext_in}")
        output_webp = os.path.join(TEMP_DIR, f"fig_out_{msg_id}.webp")

        try:
            c.download_any(media_target, input_path)
            if is_video:
                ok = mp.convert_video_to_sticker(input_path, output_webp, max_duration=5)
            else:
                ok = mp.convert_image_to_sticker(input_path, output_webp)

            if ok:
                c.send_sticker(chat_clean, output_webp, name="sticker", packname="bot")
                descontar_credito_usuario()
            else:
                c.reply_message("Nao foi possivel criar a figurinha.", message, to=chat_clean)
        except Exception as e:
            print(f"Erro no comando .fig: {e}")
            c.reply_message("Erro ao criar a figurinha.", message, to=chat_clean)
        finally:
            for f in [input_path, output_webp]:
                if os.path.exists(f):
                    try:
                        os.remove(f)
                    except Exception:
                        pass
        return

    # 6. Comando .toimg (converte figurinha respondida em imagem ou video)
    if texto_lower == ".toimg":
        if not autorizado:
            bloquear_nao_autorizado()
            return

        quoted_msg, _ = obter_mensagem_citada(raw_msg)
        if not quoted_msg or not has_field(quoted_msg, "stickerMessage"):
            c.reply_message("Responda a uma figurinha com .toimg para converter em imagem ou video.", message, to=chat_clean)
            return

        in_webp = os.path.join(TEMP_DIR, f"toimg_in_{msg_id}.webp")
        out_png = os.path.join(TEMP_DIR, f"toimg_out_{msg_id}.png")
        out_mp4 = os.path.join(TEMP_DIR, f"toimg_out_{msg_id}.mp4")

        try:
            c.download_any(quoted_msg, in_webp)
            ok, kind = mp.convert_sticker_to_media(in_webp, out_png, out_mp4)
            if ok and kind == "image":
                c.send_image(chat_clean, out_png)
                descontar_credito_usuario()
            elif ok and kind == "video":
                c.send_video(chat_clean, out_mp4)
                descontar_credito_usuario()
            else:
                c.reply_message("Nao foi possivel converter a figurinha.", message, to=chat_clean)
        except Exception as e:
            print(f"Erro no comando .toimg: {e}")
            c.reply_message("Erro ao converter a figurinha.", message, to=chat_clean)
        finally:
            for f in [in_webp, out_png, out_mp4]:
                if os.path.exists(f):
                    try:
                        os.remove(f)
                    except Exception:
                        pass
        return

    # 7. Comando .baixar <link> (baixa video de TikTok, Reels, Shorts, etc.)
    if texto_lower.startswith(".baixar"):
        if not autorizado:
            bloquear_nao_autorizado()
            return

        url_alvo = extrair_url(texto_mensagem)
        if not url_alvo:
            quoted_msg, _ = obter_mensagem_citada(raw_msg)
            if quoted_msg:
                url_alvo = extrair_url(extract_text(quoted_msg))

        if not url_alvo:
            c.reply_message("Uso: .baixar <link> (ou responda a uma mensagem que tenha um link com .baixar)", message, to=chat_clean)
            return

        out_video = os.path.join(TEMP_DIR, f"dl_{msg_id}.mp4")
        try:
            ok = mp.download_social_video(url_alvo, out_video)
            if ok and os.path.exists(out_video):
                c.send_video(chat_clean, out_video)
                descontar_credito_usuario()
            else:
                c.reply_message("Nao foi possivel baixar o video desse link.", message, to=chat_clean)
        except Exception as e:
            print(f"Erro no comando .baixar: {e}")
            c.reply_message("Erro ao baixar o video.", message, to=chat_clean)
        finally:
            if os.path.exists(out_video):
                try:
                    os.remove(out_video)
                except Exception:
                    pass
        return

    # 8. Cache do Anti-Delete, Anti-Edit e Visualizacao Unica: ignora grupos se o monitoramento nao estiver ativo
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

    horario_atual = datetime.now().strftime("%H:%M:%S")
    pushname = msg_info.Pushname or ""

    # Se for midia de visualizacao unica, encaminha imediatamente para o privado do dono
    if salved_media and os.path.exists(salved_media) and not is_from_me and is_view_once(message.Message, raw_msg):
        autor_label = f"{pushname} (+{sender_number})" if pushname else f"+{sender_number}"
        print(f"[Visualizacao Unica] Midia de {autor_label} em {chat_label} capturada!")
        alerta_vo = (
            "Midia de visualizacao unica detectada:\n"
            f"De: {autor_label}\n"
            f"Local: {chat_label}\n"
            f"Horario: {horario_atual}\n"
        )
        if texto_mensagem:
            alerta_vo += f"Legenda: {texto_mensagem}"

        dest = owner_jid if owner_jid else chat_clean
        try:
            c.send_message(dest, alerta_vo)
            if salved_media.endswith((".jpg", ".png")):
                c.send_image(dest, salved_media)
            elif salved_media.endswith(".mp4"):
                c.send_video(dest, salved_media)
            elif salved_media.endswith((".ogg", ".mp3")):
                c.send_audio(dest, salved_media)
        except Exception as err:
            print(f"[Visualizacao Unica] Erro ao encaminhar midia: {err}")

    if texto_mensagem or salved_media:
        message_cache[msg_id] = {
            "sender_number": sender_number,
            "pushname": pushname,
            "chat_name": chat_label,
            "text": texto_mensagem,
            "media_path": salved_media,
            "time": horario_atual
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
    print("Iniciando bot. Escaneie o QR Code caso solicitado.")
    threading.Thread(target=escutar_terminal, daemon=True).start()
    client.connect()
