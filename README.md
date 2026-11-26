# bot-whatsapp

Bot de WhatsApp em Python para criacao de figurinhas de audio a partir de videos curtos e encaminhamento de mensagens apagadas (anti-delete).

## Funcionalidades

- `.audiosticker`: corta os primeiros 5 segundos do video e envia o sticker com o audio correspondente.
  - Em grupo: responde "sai dai bixo".
  - No privado (sem permissao): responde "aguarde o adm".
- `.authorize`: libera o uso para um contato (padrao 2 usos). Apenas o dono pode usar.
- `.remover`: remove o contato da lista de autorizados.
- Anti-delete: quando alguem apaga uma mensagem para todos em grupos ou no privado, o bot encaminha no privado do dono o autor, o local e o conteudo apagado (texto ou midia).

## Como rodar

1. Instalar as dependencias:
   pip install -r requirements.txt

2. Iniciar o bot:
   python main.py
   (ou executar o arquivo iniciar.bat)

3. Escanear o QR code no terminal pelo WhatsApp (Aparelhos conectados > Conectar um aparelho).
