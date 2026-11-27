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
- `.lembrete` / `.remind <tempo> <mensagem>`: agenda um lembrete (ex: `10s`, `30m`, `2h`, `1d`) e envia quando vencer.
- `.ping`: responde com o tempo de resposta em milissegundos (ms).

### Administrador (exclusivos do dono)
- `.authorize` / `.autorizar [numero] [qtd]`: libera o uso para um contato (padrao 2 creditos).
- `.remover` / `.remove` / `.unauthorize [numero]`: remove o contato da lista de autorizados.
- `.lista` / `.list`: mostra todos os contatos autorizados no momento e seus creditos restantes.
- `.status` / `.info`: mostra o tempo ligado, mensagens no cache, status do anti-delete em grupos e total de usuarios autorizados/bloqueados.
- `.limpar` / `.clearcache`: limpa imediatamente todo o cache de mensagens (privado e grupos) e apaga arquivos temporarios da pasta `temp/`.
- `.bloquear` / `.block [numero]`: bloqueia um usuario no bot para que seja ignorado em silencio total.
- `.desbloquear` / `.unblock [numero]`: remove o usuario da lista de bloqueados do bot.
- `.ban` / `.kick [numero]`: remove um participante do grupo (somente admins do grupo ou o dono).
- `.add` / `.adicionar <numero>`: adiciona um numero ao grupo (somente admins do grupo ou o dono).
- `.promover` / `.promote [numero]`: promove o participante a admin do grupo (somente admins do grupo ou o dono).
- `.rebaixar` / `.demote [numero]`: remove o cargo de admin do participante no grupo (somente admins do grupo ou o dono).
- `.fechar` / `.close`: fecha o grupo para que apenas admins enviem mensagens (somente admins do grupo ou o dono).
- `.abrir` / `.open`: abre o grupo para todos os participantes enviarem mensagens (somente admins do grupo ou o dono).
- `.link` / `.invitelink`: gera e envia o link de convite do grupo (somente admins do grupo ou o dono).
- `.apagar` / `.del`: apaga a mensagem marcada para todos no grupo (somente admins do grupo ou o dono).
- `.mute` / `.mutar [tempo]`: apaga silenciosamente as mensagens do usuario no grupo pelo tempo definido (padrao `5m`, somente admins reais ou o dono).
- `.unmute` / `.desmutar`: remove o mute do usuario no grupo antes do tempo acabar.
- `.sudo [numero]`: concede permissao a um usuario para usar comandos de admin de grupo (exclusivo do dono).
- `.unsudo` / `.nosudo` / `.removersudo [numero]`: remove os superpoderes de `.sudo` do usuario (exclusivo do dono).
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
