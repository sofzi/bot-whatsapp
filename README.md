# bot-whatsapp

Bot de WhatsApp em Python para criacao de figurinhas de audio a partir de videos curtos e encaminhamento de mensagens apagadas (anti-delete).

## Funcionalidades

- `.audiosticker`: corta os primeiros 5 segundos do video e envia o sticker com o audio correspondente (funciona no privado e em grupos para usuarios autorizados).
  - Em grupo (sem permissao): responde "sai dai bixo".
  - No privado (sem permissao): responde "aguarde o adm".
- `.authorize`: libera o uso para um contato (padrao 2 usos). Apenas o dono pode usar.
- `.remover`: remove o contato da lista de autorizados.
- `.ping`: responde com o tempo de resposta em milissegundos (ms).
- `.antidelete`: ativa a leitura de mensagens apagadas e visualizacao unica para grupos (se enviado em um grupo, ativa apenas nele; se enviado no privado, ativa em todos os grupos).
- `.stopantidelete`: desativa a leitura de mensagens apagadas e visualizacao unica nos grupos.
- `.encerrar`: desliga o bot remotamente pelo WhatsApp (apenas o dono).
- Anti-delete e Visualizacao Unica: ativos por padrao apenas nas conversas privadas para nao sobrecarregar (canais/newsletters e status sao sempre ignorados). Quando alguem apaga uma mensagem ou envia midia de visualizacao unica, o bot encaminha no privado do dono o autor, o local e o conteudo.

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
