# bot-whatsapp

Bot de WhatsApp em Python para criacao de figurinhas com som, download de videos e monitoramento de mensagens apagadas e editadas.

## Comandos

### Gerais / Midia
- `.comandos` / `.ajuda` / `.help` / `.commands` / `.menu`: lista todos os comandos disponiveis.
- `.audiosticker` / `.soundsticker` / `.figsom`: cria figurinha com som nativa a partir de um video ou link (respeita a duracao original ate 10s, preenchida em 512x512 sem bordas).
  - Todo usuario novo tem direito a 1 figurinha com som gratuita. Apos usar, precisa ser autorizado pelo adm com `.authorize`.
- `.fig` / `.sticker`: cria figurinha normal de foto ou video enviado/respondido.
- `.toimg` / `.parafoto`: converte uma figurinha respondida de volta em imagem (PNG) ou video (MP4).
- `.baixar` / `.download` / `.dl <link>`: baixa videos do TikTok, Instagram Reels, YouTube Shorts, etc. e envia no chat.
- `.ping`: responde com o tempo de resposta em milissegundos (ms).

### Administrador (exclusivos do dono)
- `.authorize` / `.autorizar [numero] [qtd]`: libera o uso para um contato (padrao 2 creditos).
- `.remover` / `.remove` / `.unauthorize [numero]`: remove o contato da lista de autorizados.
- `.lista` / `.list`: mostra todos os contatos autorizados no momento e seus creditos restantes.
- `.status` / `.info`: mostra o tempo ligado, mensagens no cache, status do anti-delete em grupos e total de usuarios autorizados/bloqueados.
- `.limpar` / `.clearcache`: limpa imediatamente todo o cache de mensagens (privado e grupos) e apaga arquivos temporarios da pasta `temp/`.
- `.bloquear` / `.ban [numero]`: bloqueia um usuario para que o bot o ignore em silencio total.
- `.desbloquear` / `.unban [numero]`: remove o usuario da lista de bloqueados.
- `.logs`: envia as ultimas 20 linhas do `bot.log` no privado do dono.
- `.todos` / `.tagall <mensagem>`: envia uma mensagem em grupo marcando todos os participantes silenciosamente.
- `.antidelete`: ativa o monitoramento de mensagens apagadas e editadas para grupos (mensagens de grupos sao limpas automaticamente do cache apos 12 horas).
- `.stopantidelete`: desativa o monitoramento nos grupos.
- `.ver` / `.view` / `.save`: resgata uma midia marcada para o privado do dono.
- `.restart` / `.reiniciar`: reinicia o bot recarregando o codigo sem perder a sessao atual.
- `.desligar` / `.encerrar` / `.stop` / `.shutdown`: desconecta a sessao do WhatsApp, apaga os arquivos de login e fecha o bot.

## Monitoramento Automatico

- Anti-delete e Anti-edit ativos por padrao nas conversas privadas (canais e status sao sempre ignorados). Encaminha no privado do dono qualquer mensagem apagada ou texto editado (antes/depois).

## Como rodar

1. Instalar as dependencias:
   pip install -r requirements.txt

2. Iniciar o bot:
   python main.py
   (ou executar o arquivo iniciar.bat)

3. Escanear o QR code no terminal pelo WhatsApp (Aparelhos conectados > Conectar um aparelho).

## Como reiniciar ou encerrar

- Para reiniciar mantendo a sessao: envie `.restart` no WhatsApp ou digite `restart` no terminal.
- Para desligar e apagar a sessao: envie `.desligar` (ou `.shutdown`) no WhatsApp ou digite `sair` no terminal.
