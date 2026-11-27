import os
import re
import sys
import time
import glob
import shutil
import subprocess
import threading
from collections import deque
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

from neonize.client import NewClient, get_sidecar_from_upload
from neonize.events import ConnectedEv, MessageEv
from neonize.utils import extract_text
from neonize.utils.enum import MediaType, ParticipantChange
from neonize.utils.jid import build_jid, JIDToNonAD
from neonize.utils.ffmpeg import FFmpeg, FFProbeInfo, Format, Stream
import neonize.proto.waE2E.WAWebProtobufsE2E_pb2 as wa_pb
import database as db
import media_processor as mp

# Substitui a chamada do ffprobe interno do Neonize por leitura direta via ffmpeg.exe
def _patched_extract_info(self) -> FFProbeInfo:
    if self._info is not None:
        return self._info
    dur = 5.0
    streams = []
    try:
        r = subprocess.run([_ffmpeg_exe, "-i", self.filepath], capture_output=True, text=True, errors="ignore")
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr or "")
        if m:
            dur = float(m.group(1)) * 3600 + float(m.group(2)) * 60 + float(m.group(3))
        for line in (r.stderr or "").splitlines():
            if "Stream #" in line and "Video:" in line:
                streams.append(Stream(**{f: ("video" if f == "codec_type" else ("h264" if f == "codec_name" else None)) for f in Stream.__dataclass_fields__}))
            elif "Stream #" in line and "Audio:" in line:
                streams.append(Stream(**{f: ("audio" if f == "codec_type" else ("opus" if f == "codec_name" else None)) for f in Stream.__dataclass_fields__}))
    except Exception:
        pass
    if not streams:
        streams.append(Stream(**{f: ("video" if f == "codec_type" else None) for f in Stream.__dataclass_fields__}))
    fmt_dict = {f: None for f in Format.__dataclass_fields__}
    fmt_dict["duration"] = dur
    fmt_dict["filename"] = self.filepath
    self._info = FFProbeInfo(format=Format(**fmt_dict), streams=streams)
    return self._info

def _patched_extract_thumbnail(self, *args, **kwargs) -> bytes:
    try:
        r = subprocess.run(
            [_ffmpeg_exe, "-i", self.filepath, "-vframes", "1", "-an", "-vf", "scale=200:-1", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
            capture_output=True,
        )
        if r.returncode == 0 and r.stdout:
            return r.stdout
    except Exception:
        pass
    return b""

from io import BytesIO
from PIL import Image
import neonize.client as _neonize_client
import neonize.utils.sticker as _neonize_sticker

FFmpeg.extract_info = _patched_extract_info
FFmpeg.extract_thumbnail = _patched_extract_thumbnail

# Injeta EXIF (numero e nome do pacote) no WebP sem depender do webpmux externo
def _inject_webp_exif_riff(webp_bytes: bytes, exif_bytes: bytes) -> bytes:
    if len(webp_bytes) < 12 or webp_bytes[:4] != b"RIFF" or webp_bytes[8:12] != b"WEBP":
        return webp_bytes
    pos = 12
    chunks = []
    has_vp8x = False
    while pos + 8 <= len(webp_bytes):
        fourcc = webp_bytes[pos:pos + 4]
        size = int.from_bytes(webp_bytes[pos + 4:pos + 8], "little")
        end = pos + 8 + size
        if end > len(webp_bytes):
            break
        payload = bytearray(webp_bytes[pos + 8:end])
        if fourcc == b"VP8X" and len(payload) >= 1:
            has_vp8x = True
            payload[0] |= 0x08  # Flag EXIF
        if fourcc != b"EXIF":
            chunks.append((fourcc, bytes(payload)))
        pos = end + (size & 1)

    if not has_vp8x:
        return b""

    chunks.append((b"EXIF", exif_bytes))
    body = bytearray(b"WEBP")
    for fourcc, payload in chunks:
        body.extend(fourcc)
        body.extend(len(payload).to_bytes(4, "little"))
        body.extend(payload)
        if len(payload) & 1:
            body.append(0)
    return b"RIFF" + len(body).to_bytes(4, "little") + bytes(body)

def _patched_add_exif_to_sticker(sticker: bytes, name: str = "", packname: str = "") -> bytes | None:
    try:
        exif_bytes = _neonize_sticker.add_exif(name=name, packname=packname)
        injected = _inject_webp_exif_riff(sticker, exif_bytes)
        if injected:
            return injected
        # Fallback para WebP estatico sem chunk VP8X
        img = Image.open(BytesIO(sticker))
        out = BytesIO()
        is_anim = getattr(img, "is_animated", False) and getattr(img, "n_frames", 1) > 1
        if is_anim:
            img.save(out, format="WEBP", exif=exif_bytes, save_all=True, loop=0)
        else:
            img.save(out, format="WEBP", exif=exif_bytes)
        data = out.getvalue()
        return data if data else None
    except Exception as e:
        print(f"Erro ao gravar EXIF na figurinha: {e}")
        return None

_neonize_sticker.add_exif_to_sticker = _patched_add_exif_to_sticker
_neonize_client.add_exif_to_sticker = _patched_add_exif_to_sticker

TEMP_DIR = "temp"
os.makedirs(TEMP_DIR, exist_ok=True)

# Cache de mensagens para anti-delete e anti-edit
message_cache = {}

# Controle de lembretes ativos (1 por usuario)
active_reminders = {}
reminders_lock = threading.Lock()

# Controle de usuarios mutados temporariamente por grupo
muted_users = {}
muted_lock = threading.Lock()

# Historico leve de IDs de mensagens por grupo (para comando .clear)
group_msg_history = {}
history_lock = threading.Lock()

# Controle do monitoramento em grupos (carregado do database.json para persistir)
antidelete_grupos_global, antidelete_grupos_ativos = db.get_antidelete_config()

# Inicializa cliente do WhatsApp
client = NewClient("session.db")
owner_jid = None
owner_number = None
bot_start_time = time.time()

# Formata tempo ligado em horas, minutos e segundos
def formatar_uptime(segundos: float) -> str:
    total = int(segundos)
    h, resto = divmod(total, 3600)
    m, s = divmod(resto, 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"

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
        if has_field(msg, "documentWithCaptionMessage") and has_field(msg.documentWithCaptionMessage, "message"):
            msg = msg.documentWithCaptionMessage.message
            continue
        if has_field(msg, "editedMessage") and has_field(msg.editedMessage, "message"):
            msg = msg.editedMessage.message
            continue
        if has_field(msg, "audioStickerMessage") and has_field(msg.audioStickerMessage, "message"):
            msg = msg.audioStickerMessage.message
            continue
        break
    return msg

# Monta e envia uma figurinha com audio nativa do WhatsApp (campo 134 FutureProofMessage + campo 26 audioMessage)
def enviar_figurinha_sonora_nativa(c: NewClient, chat_dest, webp_path: str, audio_path: str, duration_sec: int, quoted=None):
    # Usa o numero conectado dinamicamente nos metadados EXIF da figurinha
    nome_meta = f"+{owner_number}" if owner_number else "audiosticker"

    # 1. Monta a StickerMessage com EXIF e upload do WebP
    sticker_wrapper = c.build_sticker_message(
        webp_path,
        quoted=quoted,
        name=nome_meta,
        packname="bot",
        passthrough=True,
    )
    sticker_wrapper.stickerMessage.isAnimated = True
    sticker_wrapper.stickerMessage.width = 512
    sticker_wrapper.stickerMessage.height = 512

    # 2. Faz upload do audio Ogg/Opus e gera o streamingSidecar obrigatorio
    with open(audio_path, "rb") as f:
        audio_bytes = f.read()

    audio_upload = c.upload(audio_bytes, MediaType.MediaAudio)
    sidecar = (
        audio_upload.StreamingSidecar
        if audio_upload.StreamingSidecar
        else get_sidecar_from_upload(audio_bytes, audio_upload.MediaKey, media_type="audio")
    )

    audio_proto = wa_pb.AudioMessage(
        URL=audio_upload.url,
        directPath=audio_upload.DirectPath,
        fileEncSHA256=audio_upload.FileEncSHA256,
        fileLength=audio_upload.FileLength,
        fileSHA256=audio_upload.FileSHA256,
        mediaKey=audio_upload.MediaKey,
        mimetype="audio/ogg; codecs=opus",
        seconds=max(1, int(duration_sec)),
        PTT=False,
        streamingSidecar=sidecar,
    )

    # 3. Embute o AudioMessage no campo 26 de StickerMessage
    sticker_wrapper.stickerMessage.audioMessage.CopyFrom(audio_proto)

    # 4. Embrulha em FutureProofMessage no campo 134 (audioStickerMessage)
    native_msg = wa_pb.Message(
        audioStickerMessage=wa_pb.FutureProofMessage(message=sticker_wrapper)
    )

    c.send_message(chat_dest, native_msg)

# Extrai o primeiro link (http/https) de um texto
def extrair_url(texto: str) -> str | None:
    match = re.search(r"(https?://[^\s]+)", texto or "")
    return match.group(1) if match else None

# Converte texto de tempo (ex: 30s, 15m, 2h, 1d) em segundos
def parse_tempo_segundos(tempo_str: str) -> int | None:
    m = re.fullmatch(r"(\d+)\s*(s|seg|m|min|h|hr|d)?", tempo_str.strip().lower())
    if not m:
        return None
    valor = int(m.group(1))
    unidade = m.group(2) or "m"
    multiplicadores = {"s": 1, "seg": 1, "m": 60, "min": 60, "h": 3600, "hr": 3600, "d": 86400}
    total = valor * multiplicadores.get(unidade, 60)
    return total if total > 0 else None

# Retorna a mensagem citada (quotedMessage) desempacotada, se houver
def obter_mensagem_citada(raw_msg):
    if has_field(raw_msg, "extendedTextMessage") and has_field(raw_msg.extendedTextMessage, "contextInfo"):
        ctx = raw_msg.extendedTextMessage.contextInfo
        if has_field(ctx, "quotedMessage"):
            return desempacotar_mensagem(ctx.quotedMessage), ctx
    return None, None

# Extrai o JID do participante alvo em comandos de grupo (resposta, mencao ou numero)
def extrair_jid_alvo_grupo(raw_msg, texto_mensagem: str):
    if has_field(raw_msg, "extendedTextMessage") and has_field(raw_msg.extendedTextMessage, "contextInfo"):
        ctx = raw_msg.extendedTextMessage.contextInfo
        if ctx.participant:
            user_part, _, srv_part = ctx.participant.partition("@")
            user_clean = user_part.split(":")[0]
            srv_clean = srv_part or "s.whatsapp.net"
            return build_jid(user_clean, srv_clean), user_clean
        if ctx.mentionedJID:
            m_jid = ctx.mentionedJID[0]
            user_part, _, srv_part = m_jid.partition("@")
            user_clean = user_part.split(":")[0]
            srv_clean = srv_part or "s.whatsapp.net"
            return build_jid(user_clean, srv_clean), user_clean

    partes = texto_mensagem.split()
    if len(partes) > 1:
        num = re.sub(r"\D", "", partes[1])
        if num:
            return build_jid(num, "s.whatsapp.net"), num
    return None, None

# Verifica se o remetente e administrador do grupo
def eh_admin_do_grupo(c: NewClient, chat, sender_number: str, sender_user: str) -> bool:
    try:
        g_info = c.get_group_info(chat)
        alvos = {x for x in [sender_number, sender_user] if x}
        for p in g_info.Participants:
            if p.IsAdmin or p.IsSuperAdmin:
                ids_p = {
                    getattr(p.JID, "User", ""),
                    getattr(p.LID, "User", ""),
                    getattr(p.PhoneNumber, "User", ""),
                }
                if alvos & ids_p:
                    return True
    except Exception as err:
        print(f"Erro ao verificar admin do grupo: {err}")
    return False

# Verifica se o alvo corresponde a algum dos IDs de referencia (incluindo LID/PN no grupo)
def alvo_corresponde_ids(c: NewClient, chat, alvo_num: str, ids_ref: set, msg_cache: dict = None, stanza_id: str = None) -> bool:
    ids_validos = {x for x in ids_ref if x}
    if not ids_validos:
        return False
    if alvo_num and alvo_num in ids_validos:
        return True
    if stanza_id and msg_cache and stanza_id in msg_cache:
        if msg_cache[stanza_id].get("sender_number") in ids_validos:
            return True
    try:
        if chat and chat.Server == "g.us" and alvo_num:
            g_info = c.get_group_info(chat)
            for p in g_info.Participants:
                pn = getattr(p.PhoneNumber, "User", "") or getattr(p.JID, "User", "")
                lid = getattr(p.LID, "User", "")
                jid_u = getattr(p.JID, "User", "")
                participante_ids = {x for x in [pn, jid_u, lid] if x}
                if (ids_validos & participante_ids) and (alvo_num in participante_ids):
                    return True
    except Exception:
        pass
    return False

# Verifica se o JID ou numero alvo pertence ao proprio bot/dono (para impedir que comandos o afetem)
def alvo_eh_o_bot(c: NewClient, chat, alvo_jid, alvo_num: str, msg_cache: dict = None, stanza_id: str = None) -> bool:
    return alvo_corresponde_ids(c, chat, alvo_num, {owner_number}, msg_cache, stanza_id)

# Thread que limpa automaticamente mensagens de grupos com mais de 12 horas
def limpar_cache_grupos_periodicamente():
    limite_segundos = 12 * 3600
    while True:
        time.sleep(600)
        agora = time.time()
        chaves_expiradas = [
            k for k, v in list(message_cache.items())
            if v.get("is_group") and (agora - v.get("created_at", agora)) >= limite_segundos
        ]
        for k in chaves_expiradas:
            item = message_cache.pop(k, None)
            if item and item.get("media_path") and os.path.exists(item["media_path"]):
                try:
                    os.remove(item["media_path"])
                except Exception:
                    pass
        if chaves_expiradas:
            print(f"[Limpeza automatica] {len(chaves_expiradas)} mensagem(ns) de grupo com mais de 12h removida(s) do cache.")

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
        print("Monitoramento (Anti-delete / Anti-edit): Ativo no privado | Use .antidelete para grupos")
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

    # Punicao secreta (macaco): apaga todas as mensagens do alvo no grupo e responde marcando
    chaves_macaco_grupo = [f"{chat.User}:{sender_number}", f"{chat.User}:{sender.User}", sender_number, sender.User]
    if is_group and not is_from_me and db.is_macaco(chaves_macaco_grupo):
        try:
            sender_clean = JIDToNonAD(sender)
            c.revoke_message(chat, sender_clean, msg_id)
            mention_jid = f"{sender_clean.User}@{sender_clean.Server}"
            msg_macaco = wa_pb.Message(
                extendedTextMessage=wa_pb.ExtendedTextMessage(
                    text=f"cala boca macaco @{sender_clean.User}",
                    contextInfo=wa_pb.ContextInfo(mentionedJID=[mention_jid]),
                )
            )
            c.send_message(chat_clean, msg_macaco)
        except Exception as err:
            print(f"[Macaco] Erro ao apagar mensagem de +{sender_number}: {err}")
        return

    # Mute temporario: apaga silenciosamente as mensagens do usuario mutado no grupo
    if is_group and not is_from_me:
        chaves_mute = [f"{chat.User}:{sender_number}", f"{chat.User}:{sender.User}"]
        agora_ts = time.time()
        esta_mutado = False
        with muted_lock:
            for km in chaves_mute:
                exp = muted_users.get(km)
                if exp is not None:
                    if agora_ts < exp:
                        esta_mutado = True
                        break
                    muted_users.pop(km, None)
        if esta_mutado:
            try:
                c.revoke_message(chat, JIDToNonAD(sender), msg_id)
            except Exception as err:
                print(f"[Mute] Erro ao apagar mensagem de +{sender_number}: {err}")
            return

    # Lista de comandos exclusivos do dono (PT e EN)
    comandos_adm = (
        ".antidelete",
        ".stopantidelete",
        ".ver",
        ".view",
        ".save",
        ".lista",
        ".list",
        ".status",
        ".info",
        ".limpar",
        ".clearcache",
        ".bloquear",
        ".block",
        ".desbloquear",
        ".unblock",
        ".sudo",
        ".unsudo",
        ".nosudo",
        ".removersudo",
        ".logs",
        ".todos",
        ".tagall",
        ".encerrar",
        ".desligar",
        ".stop",
        ".shutdown",
        ".restart",
        ".reiniciar",
        ".authorize",
        ".autorizar",
        ".remover",
        ".remove",
        ".unauthorize",
    )

    # Se outra pessoa tentar usar qualquer comando de admin, bloqueia e ignora
    if not is_from_me and any(texto_lower == cmd or texto_lower.startswith(cmd + " ") for cmd in comandos_adm):
        print(f"[Seguranca] Tentativa bloqueada de comando admin por +{sender_number}: {texto_mensagem}")
        return

    # Se o usuario estiver bloqueado (.bloquear / .ban), ignora qualquer comando em silencio
    chaves_remetente = [sender_number, sender.User]
    if not is_group:
        chaves_remetente.append(chat.User)
    if not is_from_me and texto_lower.startswith(".") and any(db.is_blocked(k) for k in chaves_remetente):
        return

    # 2. Comandos de Administrador (Exclusivos do Dono)
    if is_from_me:
        # Comando .antidelete (ativa leitura de grupos e salva no database.json)
        if texto_lower == ".antidelete":
            if is_group:
                antidelete_grupos_ativos.add(chat.User)
                db.save_antidelete_config(antidelete_grupos_global, antidelete_grupos_ativos)
                print(f"[Anti-Delete] Ativado e salvo para o grupo {chat.User}")
                c.reply_message("Anti-delete ativado e salvo neste grupo.", message, to=chat_clean)
            else:
                antidelete_grupos_global = True
                db.save_antidelete_config(antidelete_grupos_global, antidelete_grupos_ativos)
                print("[Anti-Delete] Ativado e salvo para todos os grupos.")
                c.reply_message("Anti-delete ativado e salvo para todos os grupos.", message, to=chat_clean)
            return

        # Comando .stopantidelete (para leitura de grupos e salva no database.json)
        if texto_lower == ".stopantidelete":
            if is_group:
                antidelete_grupos_ativos.discard(chat.User)
                db.save_antidelete_config(antidelete_grupos_global, antidelete_grupos_ativos)
                print(f"[Anti-Delete] Desativado e salvo para o grupo {chat.User}")
                c.reply_message("Anti-delete desativado neste grupo.", message, to=chat_clean)
            else:
                antidelete_grupos_global = False
                antidelete_grupos_ativos.clear()
                db.save_antidelete_config(antidelete_grupos_global, antidelete_grupos_ativos)
                print("[Anti-Delete] Desativado e salvo em todos os grupos.")
                c.reply_message("Anti-delete desativado em todos os grupos.", message, to=chat_clean)
            return

        # Comando .ver / .view / .save (resgata midia marcada para o privado do dono)
        if texto_lower in [".ver", ".view", ".save"]:
            quoted_msg, ctx = obter_mensagem_citada(raw_msg)
            if not quoted_msg:
                c.reply_message("Responda a uma foto, video ou audio com .ver", message, to=chat_clean)
                return

            dest = owner_jid if owner_jid else chat_clean

            # 1. Tenta buscar do cache caso o bot tenha recebido o arquivo completo
            if ctx and ctx.stanzaID and ctx.stanzaID in message_cache:
                cached_media = message_cache[ctx.stanzaID].get("media_path")
                if cached_media and os.path.exists(cached_media):
                    c.send_message(dest, "Midia resgatada do cache com .ver:")
                    if cached_media.endswith((".jpg", ".png")):
                        c.send_image(dest, cached_media)
                    elif cached_media.endswith(".mp4"):
                        c.send_video(dest, cached_media)
                    elif cached_media.endswith((".ogg", ".mp3")):
                        c.send_audio(dest, cached_media)
                    return

            ext = None
            media_obj = None
            if has_field(quoted_msg, "imageMessage"):
                ext = ".jpg"
                media_obj = quoted_msg.imageMessage
            elif has_field(quoted_msg, "videoMessage"):
                ext = ".mp4"
                media_obj = quoted_msg.videoMessage
            elif has_field(quoted_msg, "audioMessage"):
                ext = ".ogg"
                media_obj = quoted_msg.audioMessage

            if not ext or not media_obj:
                c.reply_message("A mensagem respondida nao contem foto, video ou audio.", message, to=chat_clean)
                return

            # 2. Verifica se a midia possui URL disponivel para download
            has_url = bool(getattr(media_obj, "URL", "") or getattr(media_obj, "directPath", ""))
            if not has_url:
                c.reply_message("Nao ha arquivo disponivel para baixar nesta mensagem.", message, to=chat_clean)
                return

            temp_ver = os.path.join(TEMP_DIR, f"ver_{msg_id}{ext}")
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

        # Comando .status / .info (painel rapido do bot)
        if texto_lower in [".status", ".info"]:
            uptime = formatar_uptime(time.time() - bot_start_time)
            qtd_cache = len(message_cache)
            qtd_autorizados = len(db.list_authorized())
            if antidelete_grupos_global:
                status_grupos = "Ativo (todos os grupos)"
            elif antidelete_grupos_ativos:
                status_grupos = f"Ativo ({len(antidelete_grupos_ativos)} grupo(s))"
            else:
                status_grupos = "Desativado"

            resumo = (
                "Status do bot:\n"
                f"- Tempo ligado: {uptime}\n"
                f"- Mensagens no cache: {qtd_cache}/1000\n"
                f"- Anti-delete em grupos: {status_grupos}\n"
                f"- Usuarios autorizados: {qtd_autorizados}\n"
                f"- Usuarios bloqueados: {len(db.list_blocked())}"
            )
            c.reply_message(resumo, message, to=chat_clean)
            return

        # Comando .limpar / .clearcache (limpa todo o cache e arquivos temporarios de uma vez)
        if texto_lower in [".limpar", ".clearcache"]:
            qtd_msgs = len(message_cache)
            message_cache.clear()
            bytes_removidos = 0
            arqs_removidos = 0
            if os.path.exists(TEMP_DIR):
                for nome in os.listdir(TEMP_DIR):
                    caminho = os.path.join(TEMP_DIR, nome)
                    if os.path.isfile(caminho):
                        try:
                            bytes_removidos += os.path.getsize(caminho)
                            os.remove(caminho)
                            arqs_removidos += 1
                        except Exception:
                            pass
            mb = bytes_removidos / (1024 * 1024)
            c.reply_message(
                f"Cache limpo: {qtd_msgs} mensagem(ns) e {arqs_removidos} arquivo(s) temporario(s) ({mb:.2f} MB) removidos.",
                message,
                to=chat_clean,
            )
            return

        # Comando .logs (envia as ultimas 20 linhas do bot.log no privado do dono)
        if texto_lower == ".logs":
            dest = owner_jid if owner_jid else chat_clean
            if not os.path.exists("bot.log"):
                c.send_message(dest, "Arquivo bot.log ainda nao existe.")
                return
            try:
                with open("bot.log", "r", encoding="utf-8", errors="ignore") as f:
                    ultimas = f.readlines()[-20:]
                conteudo_log = "".join(ultimas).strip() or "(log vazio)"
                if len(conteudo_log) > 3500:
                    conteudo_log = conteudo_log[-3500:]
                c.send_message(dest, f"Ultimas linhas do bot.log:\n\n{conteudo_log}")
            except Exception as err:
                c.send_message(dest, f"Erro ao ler bot.log: {err}")
            return

        # Comando .todos / .tagall <mensagem> (mencao silenciosa de todos os membros do grupo)
        if texto_lower == ".todos" or texto_lower.startswith(".todos ") or texto_lower == ".tagall" or texto_lower.startswith(".tagall "):
            if not is_group:
                c.reply_message("Este comando so funciona dentro de grupos.", message, to=chat_clean)
                return
            partes = texto_mensagem.split(" ", 1)
            aviso = partes[1].strip() if len(partes) > 1 and partes[1].strip() else "Atencao!"
            try:
                g_info = c.get_group_info(chat)
                mencoes = [f"{p.JID.User}@{p.JID.Server}" for p in g_info.Participants if p.JID.User]
                msg_mencao = wa_pb.Message(
                    extendedTextMessage=wa_pb.ExtendedTextMessage(
                        text=aviso,
                        contextInfo=wa_pb.ContextInfo(mentionedJID=mencoes),
                    )
                )
                c.send_message(chat_clean, msg_mencao)
            except Exception as err:
                print(f"Erro no comando .todos: {err}")
                c.reply_message("Nao foi possivel marcar os membros do grupo.", message, to=chat_clean)
            return

        # Comando .sudo (da superpoderes de admin de grupo ao usuario)
        if texto_lower == ".sudo" or texto_lower.startswith(".sudo "):
            partes = texto_mensagem.split()
            alvos = set()

            if has_field(raw_msg, "extendedTextMessage") and has_field(raw_msg.extendedTextMessage, "contextInfo"):
                ctx = raw_msg.extendedTextMessage.contextInfo
                if ctx.participant:
                    alvos.add(ctx.participant.split("@")[0].split(":")[0])
                if ctx.mentionedJID:
                    for m_jid in ctx.mentionedJID:
                        alvos.add(m_jid.split("@")[0].split(":")[0])
                if ctx.stanzaID and ctx.stanzaID in message_cache:
                    alvos.add(message_cache[ctx.stanzaID]["sender_number"])

            if not alvos and len(partes) > 1:
                num_limpo = re.sub(r"\D", "", partes[1])
                if num_limpo:
                    alvos.add(num_limpo)

            if not alvos and not is_group and chat.User != owner_number:
                alvos.add(chat.User)

            if alvos:
                for a in alvos:
                    db.add_sudo(a)
                principal = next(iter(alvos))
                c.reply_message(f"Superpoderes (.sudo) concedidos para +{principal}.", message, to=chat_clean)
            else:
                c.reply_message("Uso: responda a mensagem, mencione @pessoa ou digite .sudo <numero>", message, to=chat_clean)
            return

        # Comando .unsudo / .nosudo / .removersudo (tira os superpoderes de admin de grupo)
        if any(texto_lower == cmd or texto_lower.startswith(cmd + " ") for cmd in [".unsudo", ".nosudo", ".removersudo"]):
            partes = texto_mensagem.split()
            alvos = set()

            if has_field(raw_msg, "extendedTextMessage") and has_field(raw_msg.extendedTextMessage, "contextInfo"):
                ctx = raw_msg.extendedTextMessage.contextInfo
                if ctx.participant:
                    alvos.add(ctx.participant.split("@")[0].split(":")[0])
                if ctx.mentionedJID:
                    for m_jid in ctx.mentionedJID:
                        alvos.add(m_jid.split("@")[0].split(":")[0])
                if ctx.stanzaID and ctx.stanzaID in message_cache:
                    alvos.add(message_cache[ctx.stanzaID]["sender_number"])

            if not alvos and len(partes) > 1:
                num_limpo = re.sub(r"\D", "", partes[1])
                if num_limpo:
                    alvos.add(num_limpo)

            if not alvos and not is_group and chat.User != owner_number:
                alvos.add(chat.User)

            if alvos:
                for a in alvos:
                    db.remove_sudo(a)
                principal = next(iter(alvos))
                c.reply_message(f"Superpoderes (.sudo) removidos de +{principal}.", message, to=chat_clean)
            else:
                c.reply_message("Uso: responda a mensagem, mencione @pessoa ou digite .unsudo <numero>", message, to=chat_clean)
            return

        # Comando .bloquear / .block (ignora usuario em silencio no bot)
        if texto_lower.startswith(".bloquear") or texto_lower.startswith(".block"):
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
                    db.block_user(a)
                principal = next(iter(alvos))
                c.reply_message(f"Usuario +{principal} bloqueado (sera ignorado em silencio).", message, to=chat_clean)
            else:
                c.reply_message("Uso: responda a mensagem com .bloquear ou digite: .bloquear <numero>", message, to=chat_clean)
            return

        # Comando .desbloquear / .unblock
        if texto_lower.startswith(".desbloquear") or texto_lower.startswith(".unblock"):
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
                    db.unblock_user(a)
                principal = next(iter(alvos))
                c.reply_message(f"Usuario +{principal} desbloqueado.", message, to=chat_clean)
            else:
                c.reply_message("Uso: responda a mensagem com .desbloquear ou digite: .desbloquear <numero>", message, to=chat_clean)
            return

        # Comando .lista / .list (lista todos os usuarios autorizados e seus creditos)
        if texto_lower in [".lista", ".list"]:
            autorizados = db.list_authorized()
            if not autorizados:
                c.reply_message("Nenhum usuario autorizado no momento.", message, to=chat_clean)
            else:
                linhas = ["Usuarios autorizados:"]
                for num, cred in autorizados.items():
                    linhas.append(f"- +{num}: {cred} credito(s)")
                c.reply_message("\n".join(linhas), message, to=chat_clean)
            return

        # Comando .restart / .reiniciar (reinicia o bot mantendo a sessao atual)
        if texto_lower in [".restart", ".reiniciar"]:
            print("Comando .restart recebido pelo WhatsApp. Reiniciando sem perder a sessao...")
            try:
                c.reply_message("Reiniciando bot (mantendo sessao)...", message, to=chat_clean)
            except Exception:
                pass
            time.sleep(1)
            reiniciar_mantendo_sessao()

        # Comando .desligar / .encerrar / .stop / .shutdown (encerra o bot e apaga a sessao)
        if texto_lower in [".encerrar", ".desligar", ".stop", ".shutdown"]:
            print("Comando .desligar recebido pelo WhatsApp. Apagando sessao e desligando...")
            try:
                c.reply_message("Desligando bot e encerrando sessao...", message, to=chat_clean)
            except Exception:
                pass
            time.sleep(1)
            encerrar_perdendo_sessao()

        # Comando .authorize / .autorizar
        if texto_lower.startswith(".authorize") or texto_lower.startswith(".autorizar"):
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

        # Comando .remover / .remove / .unauthorize
        if texto_lower.startswith(".remover") or texto_lower.startswith(".remove") or texto_lower.startswith(".unauthorize"):
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

    # Comando secreto: .macaco, .desmacaco / .unmacaco e .macacos / .listamacaco (apenas dono e admins reais, silencioso para demais)
    if texto_lower in [".macacos", ".listamacaco"]:
        tem_perm = is_from_me or (is_group and eh_admin_do_grupo(c, chat, sender_number, sender.User))
        if tem_perm:
            lista = db.list_macaco()
            if is_group:
                prefixo = f"{chat.User}:"
                lista_exibir = [u[len(prefixo):] for u in lista if u.startswith(prefixo)]
            else:
                lista_exibir = lista
            if not lista_exibir:
                c.reply_message("Nenhum usuario no modo macaco no momento.", message, to=chat_clean)
            else:
                linhas = ["Usuarios no modo macaco:"] + [f"- +{u}" for u in lista_exibir]
                c.reply_message("\n".join(linhas), message, to=chat_clean)
            return

    eh_cmd_macaco = texto_lower == ".macaco" or texto_lower.startswith(".macaco ")
    eh_cmd_desmacaco = (
        texto_lower in [".desmacaco", ".unmacaco"]
        or texto_lower.startswith(".desmacaco ")
        or texto_lower.startswith(".unmacaco ")
    )
    if is_group and (eh_cmd_macaco or eh_cmd_desmacaco):
        if is_from_me or eh_admin_do_grupo(c, chat, sender_number, sender.User):
            alvo_jid, alvo_num = extrair_jid_alvo_grupo(raw_msg, texto_mensagem)
            stanza_citada = None
            if has_field(raw_msg, "extendedTextMessage") and has_field(raw_msg.extendedTextMessage, "contextInfo"):
                stanza_citada = raw_msg.extendedTextMessage.contextInfo.stanzaID
            if eh_cmd_desmacaco:
                if not alvo_num:
                    c.reply_message("Uso: responda a mensagem ou mencione @pessoa com .unmacaco", message, to=chat_clean)
                    return
                db.remove_macaco(f"{chat.User}:{alvo_num}")
                db.remove_macaco(alvo_num)
                c.reply_message(f"Modo macaco desativado para +{alvo_num}.", message, to=chat_clean)
                return
            if not alvo_jid or not alvo_num:
                c.reply_message("Uso: responda a mensagem ou mencione @pessoa com .macaco", message, to=chat_clean)
                return
            if alvo_eh_o_bot(c, chat, alvo_jid, alvo_num, message_cache, stanza_citada):
                c.reply_message("Nao e possivel usar este comando contra o proprio bot.", message, to=chat_clean)
                return
            db.add_macaco(f"{chat.User}:{alvo_num}")
            c.reply_message(f"Modo macaco ativado para +{alvo_num}.", message, to=chat_clean)
            return

    # 2B. Comandos de Administracao de Grupo (admins reais do grupo, dono, ou .sudo apenas para .add e .ban)
    comandos_grupo_adm = (
        ".ban", ".kick", ".add", ".adicionar", ".promover", ".promote",
        ".rebaixar", ".demote", ".fechar", ".close", ".abrir", ".open",
        ".link", ".invitelink", ".apagar", ".del", ".mute", ".mutar",
        ".unmute", ".desmutar", ".clear", ".limparmsgs",
    )
    if any(texto_lower == cmd or texto_lower.startswith(cmd + " ") for cmd in comandos_grupo_adm):
        if not is_group:
            c.reply_message("Este comando so funciona dentro de grupos.", message, to=chat_clean)
            return

        chaves_sudo = [sender_number, sender.User]
        eh_admin_real = is_from_me or eh_admin_do_grupo(c, chat, sender_number, sender.User)
        eh_sudo_apenas = (not eh_admin_real) and db.is_sudo(chaves_sudo)

        if not eh_admin_real and not eh_sudo_apenas:
            c.reply_message("sai dai bixo, so adm do grupo pode usar isso", message, to=chat_clean)
            return

        eh_cmd_add_ban = any(
            texto_lower == cmd or texto_lower.startswith(cmd + " ")
            for cmd in (".ban", ".kick", ".add", ".adicionar")
        )
        if eh_sudo_apenas and not eh_cmd_add_ban:
            c.reply_message("Usuarios .sudo so podem usar .add e .ban.", message, to=chat_clean)
            return

        alvo_jid, alvo_num = extrair_jid_alvo_grupo(raw_msg, texto_mensagem)
        stanza_citada = None
        if has_field(raw_msg, "extendedTextMessage") and has_field(raw_msg.extendedTextMessage, "contextInfo"):
            stanza_citada = raw_msg.extendedTextMessage.contextInfo.stanzaID

        # .ban / .kick
        if texto_lower == ".ban" or texto_lower.startswith(".ban ") or texto_lower == ".kick" or texto_lower.startswith(".kick "):
            if not alvo_jid:
                c.reply_message("Uso: responda a mensagem da pessoa com .ban ou digite .ban <numero>", message, to=chat_clean)
                return
            if alvo_eh_o_bot(c, chat, alvo_jid, alvo_num, message_cache, stanza_citada):
                c.reply_message("Nao e possivel usar este comando contra o proprio bot.", message, to=chat_clean)
                return
            if eh_sudo_apenas and eh_admin_do_grupo(c, chat, alvo_num, alvo_num):
                c.reply_message("Usuarios .sudo nao podem remover admins do grupo.", message, to=chat_clean)
                return
            try:
                c.update_group_participants(chat, [alvo_jid], ParticipantChange.REMOVE)
                c.reply_message(f"Usuario +{alvo_num} removido do grupo.", message, to=chat_clean)
            except Exception as err:
                print(f"Erro no comando .ban: {err}")
                c.reply_message("Nao foi possivel remover do grupo. Verifique se o numero do bot e admin do grupo.", message, to=chat_clean)
            return

        # .add / .adicionar
        if texto_lower == ".add" or texto_lower.startswith(".add ") or texto_lower == ".adicionar" or texto_lower.startswith(".adicionar "):
            if not alvo_jid:
                c.reply_message("Uso: .add <numero com DDD> (ex: .add 5521999999999)", message, to=chat_clean)
                return
            try:
                c.update_group_participants(chat, [alvo_jid], ParticipantChange.ADD)
                c.reply_message(f"Usuario +{alvo_num} adicionado ao grupo.", message, to=chat_clean)
            except Exception as err:
                print(f"Erro no comando .add: {err}")
                c.reply_message("Nao foi possivel adicionar ao grupo. Verifique o numero, privacidade da pessoa ou se o bot e admin.", message, to=chat_clean)
            return

        # .promover / .promote
        if texto_lower == ".promover" or texto_lower.startswith(".promover ") or texto_lower == ".promote" or texto_lower.startswith(".promote "):
            if not alvo_jid:
                c.reply_message("Uso: responda a mensagem da pessoa com .promover ou digite .promover <numero>", message, to=chat_clean)
                return
            if not is_from_me and not eh_admin_do_grupo(c, chat, sender_number, sender.User) and alvo_corresponde_ids(c, chat, alvo_num, {sender_number, sender.User}, message_cache, stanza_citada):
                c.reply_message("Usuarios .sudo nao podem se promover a admin.", message, to=chat_clean)
                return
            try:
                c.update_group_participants(chat, [alvo_jid], ParticipantChange.PROMOTE)
                c.reply_message(f"Usuario +{alvo_num} promovido a admin do grupo.", message, to=chat_clean)
            except Exception as err:
                print(f"Erro no comando .promover: {err}")
                c.reply_message("Nao foi possivel promover. Verifique se o numero do bot e admin do grupo.", message, to=chat_clean)
            return

        # .rebaixar / .demote
        if texto_lower == ".rebaixar" or texto_lower.startswith(".rebaixar ") or texto_lower == ".demote" or texto_lower.startswith(".demote "):
            if not alvo_jid:
                c.reply_message("Uso: responda a mensagem da pessoa com .rebaixar ou digite .rebaixar <numero>", message, to=chat_clean)
                return
            if alvo_eh_o_bot(c, chat, alvo_jid, alvo_num, message_cache, stanza_citada):
                c.reply_message("Nao e possivel usar este comando contra o proprio bot.", message, to=chat_clean)
                return
            try:
                c.update_group_participants(chat, [alvo_jid], ParticipantChange.DEMOTE)
                c.reply_message(f"Admin removido de +{alvo_num}.", message, to=chat_clean)
            except Exception as err:
                print(f"Erro no comando .rebaixar: {err}")
                c.reply_message("Nao foi possivel rebaixar. Verifique se o numero do bot e admin do grupo.", message, to=chat_clean)
            return

        # .fechar / .close (somente admins enviam mensagens no grupo)
        if texto_lower in [".fechar", ".close"]:
            try:
                c.set_group_announce(chat, True)
                c.reply_message("Grupo fechado: apenas admins podem enviar mensagens.", message, to=chat_clean)
            except Exception as err:
                print(f"Erro no comando .fechar: {err}")
                c.reply_message("Nao foi possivel fechar o grupo. Verifique se o numero do bot e admin.", message, to=chat_clean)
            return

        # .abrir / .open (todos os membros podem enviar mensagens)
        if texto_lower in [".abrir", ".open"]:
            try:
                c.set_group_announce(chat, False)
                c.reply_message("Grupo aberto: todos os participantes podem enviar mensagens.", message, to=chat_clean)
            except Exception as err:
                print(f"Erro no comando .abrir: {err}")
                c.reply_message("Nao foi possivel abrir o grupo. Verifique se o numero do bot e admin.", message, to=chat_clean)
            return

        # .link / .invitelink (gera e envia o link de convite do grupo)
        if texto_lower in [".link", ".invitelink"]:
            try:
                convite = c.get_group_invite_link(chat)
                c.reply_message(f"Link do grupo:\n{convite}", message, to=chat_clean)
            except Exception as err:
                print(f"Erro no comando .link: {err}")
                c.reply_message("Nao foi possivel obter o link. Verifique se o numero do bot e admin.", message, to=chat_clean)
            return

        # .apagar / .del (apaga a mensagem marcada para todos no grupo)
        if texto_lower in [".apagar", ".del"]:
            _, ctx = obter_mensagem_citada(raw_msg)
            if not ctx or not ctx.stanzaID:
                c.reply_message("Responda a mensagem que deseja apagar com .apagar", message, to=chat_clean)
                return
            target_num = ctx.participant.split("@")[0].split(":")[0] if ctx.participant else ""
            if not is_from_me and alvo_eh_o_bot(c, chat, None, target_num, message_cache, ctx.stanzaID):
                c.reply_message("Nao e possivel usar este comando contra o proprio bot.", message, to=chat_clean)
                return
            try:
                if ctx.participant:
                    user_part, _, srv_part = ctx.participant.partition("@")
                    sender_target = build_jid(user_part.split(":")[0], srv_part or "s.whatsapp.net")
                else:
                    sender_target = owner_jid if owner_jid else chat_clean
                c.revoke_message(chat, sender_target, ctx.stanzaID)
            except Exception as err:
                print(f"Erro no comando .apagar: {err}")
                c.reply_message("Nao foi possivel apagar a mensagem. Verifique se voce/bot e admin do grupo.", message, to=chat_clean)
            return

        # .mute / .mutar [tempo] (apaga mensagens da pessoa silenciosamente pelo tempo informado, padrao 5m)
        if any(texto_lower == cmd or texto_lower.startswith(cmd + " ") for cmd in [".mute", ".mutar"]):
            if not alvo_jid or not alvo_num:
                c.reply_message("Uso: responda a mensagem ou mencione @pessoa com .mute [tempo] (ex: .mute 10m)", message, to=chat_clean)
                return
            if alvo_eh_o_bot(c, chat, alvo_jid, alvo_num, message_cache, stanza_citada):
                c.reply_message("Nao e possivel usar este comando contra o proprio bot.", message, to=chat_clean)
                return
            tempo_label = "5m"
            segundos_mute = 300
            for tok in texto_mensagem.split()[1:]:
                if not tok.startswith("@"):
                    seg = parse_tempo_segundos(tok)
                    if seg:
                        segundos_mute = min(seg, 7 * 86400)
                        tempo_label = tok
                        break
            chave_mute = f"{chat.User}:{alvo_num}"
            with muted_lock:
                muted_users[chave_mute] = time.time() + segundos_mute
            c.reply_message(f"Usuario +{alvo_num} mutado neste grupo por {tempo_label}.", message, to=chat_clean)
            return

        # .unmute / .desmutar
        if any(texto_lower == cmd or texto_lower.startswith(cmd + " ") for cmd in [".unmute", ".desmutar"]):
            if not alvo_num:
                c.reply_message("Uso: responda a mensagem ou mencione @pessoa com .unmute", message, to=chat_clean)
                return
            chave_mute = f"{chat.User}:{alvo_num}"
            with muted_lock:
                removido = muted_users.pop(chave_mute, None) is not None
            if removido:
                c.reply_message(f"Mute removido de +{alvo_num}.", message, to=chat_clean)
            else:
                c.reply_message(f"Usuario +{alvo_num} nao estava mutado.", message, to=chat_clean)
            return

        # .clear / .limparmsgs (apaga todas as mensagens recentes da pessoa no grupo que o bot viu)
        if any(texto_lower == cmd or texto_lower.startswith(cmd + " ") for cmd in [".clear", ".limparmsgs"]):
            if not alvo_jid or not alvo_num:
                c.reply_message("Uso: responda a mensagem ou mencione @pessoa com .clear", message, to=chat_clean)
                return
            if alvo_eh_o_bot(c, chat, alvo_jid, alvo_num, message_cache, stanza_citada):
                c.reply_message("Nao e possivel usar este comando contra o proprio bot.", message, to=chat_clean)
                return

            with history_lock:
                historico = list(group_msg_history.get(chat.User, []))

            para_apagar = []
            for item in reversed(historico):
                mid, s_jid, s_num, s_usr = item
                if alvo_num in {s_num, s_usr} or alvo_corresponde_ids(c, chat, alvo_num, {s_num, s_usr}):
                    para_apagar.append((mid, s_jid))

            if not para_apagar:
                c.reply_message(f"Nenhuma mensagem recente de +{alvo_num} encontrada no cache deste grupo.", message, to=chat_clean)
                return

            apagadas = 0
            ids_apagados = set()
            for mid, s_jid in para_apagar:
                try:
                    c.revoke_message(chat, s_jid, mid)
                    apagadas += 1
                    ids_apagados.add(mid)
                    time.sleep(0.15)
                except Exception as err:
                    print(f"[Clear] Erro ao apagar mensagem {mid}: {err}")

            if ids_apagados:
                with history_lock:
                    if chat.User in group_msg_history:
                        group_msg_history[chat.User] = deque(
                            [x for x in group_msg_history[chat.User] if x[0] not in ids_apagados],
                            maxlen=500,
                        )

            c.reply_message(f"{apagadas} mensagem(ns) de +{alvo_num} apagada(s).", message, to=chat_clean)
            return

    # 3. Comando .comandos / .ajuda / .help / .commands
    if texto_lower in [".comandos", ".ajuda", ".help", ".commands", ".menu"]:
        linhas = [
            "Comandos disponiveis:",
            "- .audiosticker / .soundsticker / .figsom : cria figurinha com som (video ou link, ate 10s)",
            "- .fig / .sticker : cria figurinha normal de foto ou video",
            "- .fign : cria figurinha mantendo a proporcao original (sem recortar)",
            "- .toimg / .parafoto : converte figurinha respondida em foto ou video",
            "- .baixar / .download / .dl <link> : baixa video de TikTok, Reels, Shorts, etc.",
            "- .mp3 / .audio <link ou video> : baixa audio MP3 (YouTube, SoundCloud, Spotify)",
            "- .lembrete / .remind <tempo> <msg> : agenda um lembrete (ex: 30m, 2h, 10s)",
            "- .ping : mostra o tempo de resposta em ms",
            "- .comandos / .help : lista os comandos",
        ]
        if is_from_me:
            linhas.extend([
                "",
                "Comandos de Admin:",
                "- .authorize / .autorizar [numero] [qtd] : autoriza usuario (padrao 2 creditos)",
                "- .remover / .remove / .unauthorize [numero] : remove autorizacao",
                "- .lista / .list : lista usuarios autorizados e creditos",
                "- .status / .info : mostra tempo ligado, cache e monitoramento",
                "- .limpar / .clearcache : limpa todo o cache e arquivos temporarios",
                "- .bloquear / .block [numero] : ignora usuario em silencio no bot",
                "- .desbloquear / .unblock [numero] : remove usuario do bloqueio no bot",
                "- .ban / .kick [numero] : remove participante do grupo",
                "- .add / .adicionar <numero> : adiciona numero ao grupo",
                "- .promover / .promote [numero] : torna participante admin do grupo",
                "- .rebaixar / .demote [numero] : remove admin do participante no grupo",
                "- .fechar / .close : fecha o grupo (so admins falam)",
                "- .abrir / .open : abre o grupo para todos",
                "- .link / .invitelink : envia o link de convite do grupo",
                "- .apagar / .del : apaga a mensagem marcada para todos no grupo",
                "- .mute / .mutar [tempo] : apaga mensagens do alvo silenciosamente (padrao 5m)",
                "- .unmute / .desmutar : remove o mute do usuario no grupo",
                "- .clear / .limparmsgs [numero] : apaga mensagens recentes do usuario no grupo",
                "- .sudo [numero] : da permissao de usar comandos de adm de grupo",
                "- .unsudo / .nosudo [numero] : remove superpoderes do usuario",
                "- .logs : envia as ultimas 20 linhas do bot.log no seu privado",
                "- .todos / .tagall <msg> : marca todos no grupo silenciosamente",
                "- .antidelete : ativa anti-delete e anti-edit em grupos",
                "- .stopantidelete : desativa anti-delete e anti-edit em grupos",
                "- .ver / .view / .save : resgata midia marcada para o seu privado",
                "- .restart / .reiniciar : reinicia mantendo a sessao",
                "- .desligar / .encerrar / .stop / .shutdown : desliga e apaga a sessao",
            ])
        c.reply_message("\n".join(linhas), message, to=chat_clean)
        return

    # 4. Comando .ping
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

    # 4B. Comando .lembrete / .remind <tempo> <texto> (apenas dono ou .sudo, maximo 1 ativo por pessoa)
    if any(texto_lower == cmd or texto_lower.startswith(cmd + " ") for cmd in [".lembrete", ".remind"]):
        if not (is_from_me or db.is_sudo([sender_number, sender.User])):
            c.reply_message("sai dai bixo, so quem tem .sudo pode usar isso", message, to=chat_clean)
            return

        partes = texto_mensagem.split(None, 2)
        if len(partes) < 3:
            c.reply_message("Uso: .lembrete <tempo> <mensagem> (ex: .lembrete 30m desligar o forno)", message, to=chat_clean)
            return

        segundos = parse_tempo_segundos(partes[1])
        texto_lembrete = partes[2].strip()
        if not segundos or segundos > 7 * 86400 or not texto_lembrete:
            c.reply_message("Tempo invalido (maximo 7d). Use algo como: 10s, 30m, 2h ou 1d.", message, to=chat_clean)
            return

        chave_lembrete = sender_number or sender.User
        with reminders_lock:
            if active_reminders.get(chave_lembrete):
                c.reply_message("Voce ja tem um lembrete ativo. Aguarde ele disparar para criar outro.", message, to=chat_clean)
                return
            active_reminders[chave_lembrete] = True

        destino_lembrete = (owner_jid if (is_from_me and owner_jid) else chat_clean)

        def disparar_lembrete():
            try:
                c.send_message(destino_lembrete, f"Lembrete:\n{texto_lembrete}")
            except Exception as err:
                print(f"Erro ao enviar lembrete: {err}")
            finally:
                with reminders_lock:
                    active_reminders.pop(chave_lembrete, None)

        t = threading.Timer(segundos, disparar_lembrete)
        t.daemon = True
        t.start()
        c.reply_message(f"Lembrete agendado para daqui a {partes[1]}.", message, to=chat_clean)
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

    # 5. Comando .audiosticker / .soundsticker / .figsom (1 uso gratis para qualquer pessoa, depois exige autorizacao)
    if any(cmd in texto_lower for cmd in [".audiosticker", ".soundsticker", ".figsom"]):
        usando_trial = False
        credito_reservado = None
        if not is_from_me:
            credito_reservado = db.try_reserve_credit(chaves_usuario)
            if not credito_reservado:
                if db.try_reserve_trial(chaves_usuario):
                    usando_trial = True
                else:
                    c.reply_message(
                        "sai dai, ja gastou seu credito",
                        message,
                        to=chat_clean,
                    )
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
            if credito_reservado:
                db.refund_credit(credito_reservado)
            c.reply_message("Envie um video, responda a um video ou cole um link junto com .audiosticker", message, to=chat_clean)
            return

        input_video = os.path.join(TEMP_DIR, f"input_{msg_id}.mp4")
        output_webp = os.path.join(TEMP_DIR, f"sticker_{msg_id}.webp")
        output_audio = os.path.join(TEMP_DIR, f"audio_{msg_id}.ogg")

        try:
            motivo_dl = "ok"
            if video_target:
                c.download_any(video_target, input_video)
            else:
                _, motivo_dl = mp.download_social_video(url_alvo, input_video)

            if not os.path.exists(input_video):
                if credito_reservado:
                    db.refund_credit(credito_reservado)
                if motivo_dl == "insuportado":
                    c.reply_message("Insuportado (limite: 100MB e 10min).", message, to=chat_clean)
                else:
                    c.reply_message("Nao foi possivel obter o video.", message, to=chat_clean)
                return

            sticker_ok = mp.convert_video_to_sticker(input_video, output_webp, max_duration=10)
            audio_ok, audio_dur = mp.extract_audio(input_video, output_audio, max_duration=10)

            if sticker_ok:
                nome_meta = f"+{owner_number}" if owner_number else "audiosticker"
                if audio_ok:
                    enviar_figurinha_sonora_nativa(c, chat_clean, output_webp, output_audio, audio_dur, quoted=message)
                else:
                    c.send_sticker(chat_clean, output_webp, name=nome_meta, packname="bot", passthrough=True)
            else:
                if credito_reservado:
                    db.refund_credit(credito_reservado)
                c.reply_message("Nao foi possivel converter o video.", message, to=chat_clean)

        except Exception as e:
            print(f"Erro ao processar audiosticker: {e}")
            if credito_reservado:
                db.refund_credit(credito_reservado)
            c.reply_message("Erro ao processar o video.", message, to=chat_clean)
        finally:
            for temp_file in glob.glob(os.path.join(TEMP_DIR, f"*_{msg_id}*")):
                if os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except Exception:
                        pass
        return

    # 6. Comando .fig / .sticker / .fign (figurinha estatica ou animada sem audio separado)
    if any(texto_lower == cmd or texto_lower.startswith(cmd + " ") for cmd in [".fig", ".sticker", ".fign"]):
        if not autorizado:
            bloquear_nao_autorizado()
            return

        recortar = not (texto_lower == ".fign" or texto_lower.startswith(".fign "))

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
                ok = mp.convert_video_to_sticker(input_path, output_webp, max_duration=10, crop=recortar)
            else:
                ok = mp.convert_image_to_sticker(input_path, output_webp, crop=recortar)

            if ok:
                nome_meta = f"+{owner_number}" if owner_number else "sticker"
                c.send_sticker(chat_clean, output_webp, name=nome_meta, packname="bot", passthrough=True)
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

    # 7. Comando .toimg / .parafoto (converte figurinha respondida em imagem ou video)
    if texto_lower in [".toimg", ".parafoto"]:
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

    # 8. Comando .baixar / .download / .dl <link> (baixa video de TikTok, Reels, Shorts, etc.)
    if any(texto_lower == cmd or texto_lower.startswith(cmd + " ") for cmd in [".baixar", ".download", ".dl"]):
        credito_reservado = None
        if not is_from_me:
            credito_reservado = db.try_reserve_credit(chaves_usuario)
            if not credito_reservado:
                bloquear_nao_autorizado()
                return

        url_alvo = extrair_url(texto_mensagem)
        if not url_alvo:
            quoted_msg, _ = obter_mensagem_citada(raw_msg)
            if quoted_msg:
                url_alvo = extrair_url(extract_text(quoted_msg))

        if not url_alvo:
            if credito_reservado:
                db.refund_credit(credito_reservado)
            c.reply_message("Uso: .baixar <link> (ou responda a uma mensagem que tenha um link com .baixar)", message, to=chat_clean)
            return

        out_video = os.path.join(TEMP_DIR, f"dl_{msg_id}.mp4")
        try:
            ok, motivo_dl = mp.download_social_video(url_alvo, out_video)
            if ok and os.path.exists(out_video):
                c.send_video(chat_clean, out_video)
            else:
                if credito_reservado:
                    db.refund_credit(credito_reservado)
                if motivo_dl == "insuportado":
                    c.reply_message("Insuportado (limite: 100MB e 10min).", message, to=chat_clean)
                else:
                    c.reply_message("Nao foi possivel baixar o video desse link.", message, to=chat_clean)
        except Exception as e:
            print(f"Erro no comando .baixar: {e}")
            if credito_reservado:
                db.refund_credit(credito_reservado)
            c.reply_message("Erro ao baixar o video.", message, to=chat_clean)
        finally:
            for temp_file in glob.glob(os.path.join(TEMP_DIR, f"dl_{msg_id}*")):
                if os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except Exception:
                        pass
        return

    # 8B. Comando .mp3 / .audio <link ou video> (YouTube, SoundCloud, Spotify ou video respondido)
    if any(texto_lower == cmd or texto_lower.startswith(cmd + " ") for cmd in [".mp3", ".audio"]):
        credito_reservado = None
        if not is_from_me:
            credito_reservado = db.try_reserve_credit(chaves_usuario)
            if not credito_reservado:
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
            if credito_reservado:
                db.refund_credit(credito_reservado)
            c.reply_message("Uso: .mp3 <link do YouTube/SoundCloud/Spotify> ou responda a um video com .mp3", message, to=chat_clean)
            return

        in_video = os.path.join(TEMP_DIR, f"mp3in_{msg_id}.mp4")
        out_mp3 = os.path.join(TEMP_DIR, f"mp3out_{msg_id}.mp3")
        try:
            motivo_dl = "ok"
            if video_target:
                c.download_any(video_target, in_video)
                ok = os.path.exists(in_video) and mp.extract_mp3_from_video(in_video, out_mp3)
            else:
                ok, motivo_dl = mp.download_social_audio(url_alvo, out_mp3)

            if ok and os.path.exists(out_mp3):
                c.send_audio(chat_clean, out_mp3)
            else:
                if credito_reservado:
                    db.refund_credit(credito_reservado)
                if motivo_dl == "insuportado":
                    c.reply_message("Insuportado (limite: 100MB e 10min).", message, to=chat_clean)
                else:
                    c.reply_message("Nao foi possivel obter o audio desse link.", message, to=chat_clean)
        except Exception as e:
            print(f"Erro no comando .mp3: {e}")
            if credito_reservado:
                db.refund_credit(credito_reservado)
            c.reply_message("Erro ao baixar/converter o audio.", message, to=chat_clean)
        finally:
            for temp_file in glob.glob(os.path.join(TEMP_DIR, f"mp3*_{msg_id}*")):
                if os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except Exception:
                        pass
        return

    # Registra ID leve da mensagem no grupo para o comando .clear
    if is_group:
        with history_lock:
            if chat.User not in group_msg_history:
                group_msg_history[chat.User] = deque(maxlen=500)
            group_msg_history[chat.User].append((msg_id, JIDToNonAD(sender), sender_number, sender.User))

    # 9. Cache do Anti-Delete e Anti-Edit: ignora grupos se o monitoramento nao estiver ativo
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

    if texto_mensagem or salved_media:
        message_cache[msg_id] = {
            "sender_number": sender_number,
            "pushname": pushname,
            "chat_name": chat_label,
            "is_group": is_group,
            "created_at": time.time(),
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
    threading.Thread(target=limpar_cache_grupos_periodicamente, daemon=True).start()
    client.connect()
