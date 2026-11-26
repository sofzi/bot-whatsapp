import os
import subprocess
import imageio_ffmpeg
from PIL import Image
import yt_dlp

# Pega o caminho do binario do FFmpeg portatil
FFMPEG_BIN = imageio_ffmpeg.get_ffmpeg_exe()

# Converte video para figurinha animada webp de ate 5 segundos
def convert_video_to_sticker(input_path: str, output_webp_path: str, max_duration: int = 5) -> bool:
    try:
        cmd = [
            FFMPEG_BIN,
            "-y",
            "-t", str(max_duration),
            "-i", input_path,
            "-vf", "fps=15,scale=512:512:force_original_aspect_ratio=decrease,pad=512:512:(ow-iw)/2:(oh-ih)/2:color=0x00000000",
            "-c:v", "libwebp_anim",
            "-loop", "0",
            "-preset", "picture",
            "-q:v", "50",
            "-compression_level", "4",
            "-an",
            output_webp_path
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return result.returncode == 0 and os.path.exists(output_webp_path)
    except Exception as e:
        print(f"Erro ao converter video em sticker: {e}")
        return False

# Converte imagem estatica para figurinha webp 512x512
def convert_image_to_sticker(input_path: str, output_webp_path: str) -> bool:
    try:
        cmd = [
            FFMPEG_BIN,
            "-y",
            "-i", input_path,
            "-vf", "scale=512:512:force_original_aspect_ratio=decrease,pad=512:512:(ow-iw)/2:(oh-ih)/2:color=0x00000000",
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

# Extrai o audio do video nos primeiros 5 segundos para formato compativel com WhatsApp
def extract_audio(input_path: str, output_audio_path: str, max_duration: int = 5) -> bool:
    try:
        cmd = [
            FFMPEG_BIN,
            "-y",
            "-t", str(max_duration),
            "-i", input_path,
            "-vn",
            "-c:a", "libopus",
            "-b:a", "64k",
            output_audio_path
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return result.returncode == 0 and os.path.exists(output_audio_path)
    except Exception as e:
        print(f"Erro ao extrair audio: {e}")
        return False

# Baixa video curto de redes sociais (TikTok, Reels, Shorts, etc.)
def download_social_video(url: str, output_mp4_path: str) -> bool:
    try:
        if os.path.exists(output_mp4_path):
            os.remove(output_mp4_path)

        ydl_opts = {
            "format": "best[ext=mp4][filesize<50M]/best[ext=mp4]/best",
            "outtmpl": output_mp4_path,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": True,
            "ffmpeg_location": FFMPEG_BIN,
            "merge_output_format": "mp4",
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        return os.path.exists(output_mp4_path)
    except Exception as e:
        print(f"Erro ao baixar video da URL: {e}")
        return False
