import os
import subprocess
import imageio_ffmpeg

# Pega o caminho do binario do FFmpeg portatil
FFMPEG_BIN = imageio_ffmpeg.get_ffmpeg_exe()

# Converte video para figurinha animada webp de ate 5 segundos
def convert_video_to_sticker(input_path: str, output_webp_path: str, max_duration: int = 5) -> bool:
    try:
        # Comando do FFmpeg para cortar nos primeiros 5s e formatar em 512x512
        cmd = [
            FFMPEG_BIN,
            "-y",
            "-t", str(max_duration),
            "-i", input_path,
            "-vf", "fps=15,scale=512:512:force_original_aspect_ratio=decrease,pad=512:512:(ow-iw)/2:(oh-ih)/2:color=0x00000000",
            "-loop", "0",
            "-an",
            "-vsync", "0",
            output_webp_path
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return result.returncode == 0 and os.path.exists(output_webp_path)
    except Exception as e:
        print(f"Erro ao converter sticker: {e}")
        return False

# Extrai o audio do video nos primeiros 5 segundos para formato compativel com WhatsApp
def extract_audio(input_path: str, output_audio_path: str, max_duration: int = 5) -> bool:
    try:
        # Extrai o audio para Opus em container ogg
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
