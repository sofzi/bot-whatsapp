# bot-whatsapp

Bot de WhatsApp em Python para criacao de figurinhas, download de videos e monitoramento de mensagens apagadas, editadas e de visualizacao unica.

## Funcionalidades

- `.audiosticker`: corta os primeiros 5 segundos do video (ou de um link de video) e envia o sticker com o audio correspondente (funciona no privado e em grupos para usuarios autorizados).
  - Em grupo (sem permissao): responde "sai dai bixo".
  - No privado (sem permissao): responde "aguarde o adm".
- `.fig`: cria figurinha normal a partir de foto ou video enviado/respondido.
- `.toimg`: converte uma figurinha respondida de volta em imagem (PNG) ou video (MP4).
- `.baixar <link>`: baixa videos curtos do TikTok, Instagram Reels, YouTube Shorts, etc. e envia no chat.
- `.ver`: quando o dono responde a uma midia de visualizacao unica com `.ver`, o bot baixa e envia a midia aberta no privado do dono (mesmo em grupos onde o monitoramento esteja desligado).
- `.authorize`: libera o uso para um contato (padrao 2 usos). Apenas o dono pode usar.
- `.remover`: remove o contato da lista de autorizados.
- `.lista`: mostra todos os contatos autorizados no momento e seus creditos restantes.
- `.ping`: responde com o tempo de resposta em milissegundos (ms).
- `.antidelete`: ativa o monitoramento de mensagens apagadas, editadas e visualizacao unica para grupos (se enviado em um grupo, ativa apenas nele; se enviado no privado, ativa em todos os grupos).
- `.stopantidelete`: desativa o monitoramento nos grupos.
- `.encerrar` / `.desligar`: desliga o bot remotamente pelo WhatsApp (apenas o dono).
- Monitoramento Automatico (Anti-delete, Anti-edit e Visualizacao Unica): ativo por padrao nas conversas privadas (canais e status sao sempre ignorados). Encaminha no privado do dono qualquer mensagem apagada, texto editado (antes/depois) ou midia de visualizacao unica.

## Como rodar

1. Instalar as dependencias:
   pip install -r requirements.txt

2. Iniciar o bot:
   python main.py
   (ou executar o arquivo iniciar.bat)

3. Escanear o QR code no terminal pelo WhatsApp (Aparelhos conectados > Conectar um aparelho).

## Como encerrar

- Pelo terminal: digite `sair` ou `exit` e pressione Enter (ou use Ctrl + C).
- Pelo WhatsApp: envie `.encerrar` ou `.desligar` no seu chat.
