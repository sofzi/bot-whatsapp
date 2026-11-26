import os
import subprocess
import imageio_ffmpeg
from PIL import Image
import yt_dlp

# Pega o caminho do binario do FFmpeg portatil
FFMPEG_BIN = imageio_ffmpeg.get_ffmpeg_exe()

# Le a duracao em segundos de um arquivo de midia via ffmpeg
def get_media_duration(input_path: str, default: float = 5.0) -> float:
    try:
        import re
        probe = subprocess.run([FFMPEG_BIN, "-i", input_path], capture_output=True, text=True, errors="ignore")
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", probe.stderr or "")
        if m:
            return float(m.group(1)) * 3600 + float(m.group(2)) * 60 + float(m.group(3))
    except Exception:
        pass
    return default

# Converte video para figurinha animada webp respeitando duracao do video (maximo 10s e 500 KB)
def convert_video_to_sticker(input_path: str, output_webp_path: str, max_duration: int = 10) -> bool:
    dur = min(float(max_duration), get_media_duration(input_path, default=float(max_duration)))

    # Escolhe o degrau inicial pela duracao para acertar na 1a tentativa sem perder tempo
    todos_degraus = [
        (15, 50, 512),
        (12, 40, 448),
        (10, 30, 384),
        (8, 22, 320),
        (6, 15, 256),
    ]
    if dur <= 4.0:
        degraus = todos_degraus
    elif dur <= 7.0:
        degraus = todos_degraus[1:]
    else:
        degraus = todos_degraus[2:]

    try:
        for fps, q, detail in degraus:
            vf = (
                f"fps={fps},scale={detail}:{detail}:force_original_aspect_ratio=increase:flags=bilinear,"
                f"crop={detail}:{detail},scale=512:512:flags=bilinear,setsar=1"
            )
            cmd = [
                FFMPEG_BIN,
                "-y",
                "-t", str(max_duration),
                "-i", input_path,
                "-vf", vf,
                "-c:v", "libwebp_anim",
                "-loop", "0",
                "-preset", "picture",
                "-q:v", str(q),
                "-compression_level", "2",
                "-an",
                output_webp_path
            ]
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if result.returncode == 0 and os.path.exists(output_webp_path):
                if os.path.getsize(output_webp_path) <= 495_000:
                    return True
        return os.path.exists(output_webp_path) and os.path.getsize(output_webp_path) > 0
    except Exception as e:
        print(f"Erro ao converter video em sticker: {e}")
        return False

# Converte imagem estatica para figurinha webp 512x512 preenchida sem bordas
def convert_image_to_sticker(input_path: str, output_webp_path: str) -> bool:
    try:
        cmd = [
            FFMPEG_BIN,
            "-y",
            "-i", input_path,
            "-vf", "scale=512:512:force_original_aspect_ratio=increase:flags=lanczos,crop=512:512,setsar=1",
            "-vframes", "1",
            output_webp_path
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return result.returncode == 0 and os.path.exists(output_webp_path)
    except Exception as e:
        print(f"Erro ao converter imagem em sticker: {e}")
        return False

# Converte figurinha webp de volta para imagem PNG (ou MP4 se for animada)
def convert_sticker_to_media(input_webp_path: str, output_png_path: str, output_mp4_path: str) -> tuple[bool, str]:
    try:
        img = Image.open(input_webp_path)
        is_animated = getattr(img, "is_animated", False) and getattr(img, "n_frames", 1) > 1

        if not is_animated:
            img.convert("RGBA").save(output_png_path, "PNG")
            img.close()
            return os.path.exists(output_png_path), "image"

        # Se for figurinha animada, extrai os quadros e monta um MP4
        frames_dir = input_webp_path + "_frames"
        os.makedirs(frames_dir, exist_ok=True)
        try:
            for i in range(img.n_frames):
                img.seek(i)
                frame_rgba = img.convert("RGBA")
                bg = Image.new("RGB", frame_rgba.size, (0, 0, 0))
                bg.paste(frame_rgba, mask=frame_rgba.split()[3])
                bg.save(os.path.join(frames_dir, f"frame_{i:04d}.png"), "PNG")
            img.close()

            cmd = [
                FFMPEG_BIN,
                "-y",
                "-framerate", "15",
                "-i", os.path.join(frames_dir, "frame_%04d.png"),
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                output_mp4_path
            ]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        finally:
            for f in os.listdir(frames_dir):
                try:
                    os.remove(os.path.join(frames_dir, f))
                except Exception:
                    pass
            try:
                os.rmdir(frames_dir)
            except Exception:
                pass

        return os.path.exists(output_mp4_path), "video"
    except Exception as e:
        print(f"Erro ao converter sticker para midia: {e}")
        return False, ""

# Extrai o audio do video respeitando sua duracao real (maximo 10s) em Ogg/Opus 48kHz
def extract_audio(input_path: str, output_audio_path: str, max_duration: int = 10) -> tuple[bool, int]:
    try:
        cmd = [
            FFMPEG_BIN,
            "-y",
            "-t", str(max_duration),
            "-i", input_path,
            "-vn",
            "-ar", "48000",
            "-ac", "1",
            "-c:a", "libopus",
            "-b:a", "64k",
            output_audio_path
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0 or not os.path.exists(output_audio_path) or os.path.getsize(output_audio_path) == 0:
            return False, 0

        # Le a duracao real do audio extraido
        dur = max_duration
        probe = subprocess.run([FFMPEG_BIN, "-i", output_audio_path], capture_output=True, text=True, errors="ignore")
        import re
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", probe.stderr or "")
        if m:
            dur_float = float(m.group(1)) * 3600 + float(m.group(2)) * 60 + float(m.group(3))
            dur = max(1, min(max_duration, int(round(dur_float))))
        return True, dur
    except Exception as e:
        print(f"Erro ao extrair audio: {e}")
        return False, 0

# Baixa video curto de redes sociais (TikTok, Reels, Shorts, etc.) com limite de tamanho e duracao
def download_social_video(url: str, output_mp4_path: str, max_filesize_mb: int = 50, max_duration_sec: int = 300) -> bool:
    try:
        if os.path.exists(output_mp4_path):
            os.remove(output_mp4_path)

        ydl_opts = {
            "format": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio/bestvideo*+bestaudio/best",
            "outtmpl": output_mp4_path,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": True,
            "socket_timeout": 20,
            "max_filesize": max_filesize_mb * 1024 * 1024,
            "match_filter": yt_dlp.utils.match_filter_func(f"duration <= {max_duration_sec}"),
            "ffmpeg_location": FFMPEG_BIN,
            "merge_output_format": "mp4",
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        return os.path.exists(output_mp4_path) and os.path.getsize(output_mp4_path) > 0
    except Exception as e:
        print(f"Erro ao baixar video da URL: {e}")
        return False
