# Servidor Python do Sistema de Inspeção de Segurança (ISE)

Este servidor roda o mesmo site do repositório (`index.html`) fora do Claude. Ele substitui o que a plataforma do Claude oferecia:

| Recurso | Dentro do Claude | Neste servidor |
|---|---|---|
| Banco compartilhado | banco da plataforma | SQLite (`ISE_BANCO`), com sincronização a cada 4 segundos entre os aparelhos |
| Contas | conta do Claude | login com e-mail e senha. Um único administrador; ele cadastra, desativa e redefine a senha dos TSTs em **Usuários e backup** |
| Permissões | regras da plataforma | aplicadas no servidor: só o administrador grava `config` e `aprovacoes` e exclui registros |
| Análise do Claude | recurso da plataforma | API do Claude, com a chave guardada só no servidor (`ANTHROPIC_API_KEY`) |
| Backup no Drive | não era possível de forma íntegra | automático às 23h (America/Sao_Paulo), com conferência de MD5 de cada arquivo |

Tudo que é gravado também entra numa tabela de auditoria no servidor: conta, data, hora, operação e registro. Isso inclui login, gravação, exclusão, análise do Claude e cadastro de TST.

## 1. Testar no seu computador

É preciso ter o Python 3.11 ou mais novo.

```bash
cd servidor
python -m venv .venv
source .venv/bin/activate          # no Windows: .venv\Scripts\activate
pip install -r requirements.txt
export ISE_SECRET_KEY="uma-frase-longa-e-secreta"   # Windows: set ISE_SECRET_KEY=...
flask --app app criar-admin        # pede e-mail, nome e senha do administrador
flask --app app run                # abra http://127.0.0.1:5000
```

## 2. Publicar a partir do GitHub (Render)

1. Crie uma conta em https://render.com e conecte o GitHub.
2. Escolha **New → Blueprint** e selecione o repositório `INSPE-O-DE-SEGURAN-A`. O arquivo `render.yaml` cria o serviço com:
   - disco persistente para o banco;
   - chave de sessão gerada automaticamente.
3. Preencha as variáveis que o Render pedir (veja a seção 4). Dá para deixar a do Claude e as do Drive para depois.
4. Quando o serviço estiver no ar, abra a aba **Shell** do serviço e rode `flask --app app criar-admin`.
5. Cada `git push` na branch principal publica a nova versão sozinho.

O plano gratuito do Render não tem disco persistente. Sem disco, o banco se perde a cada publicação, por isso o `render.yaml` usa o plano *starter*.

Qualquer servidor com Docker também serve:

```bash
docker build -t ise .
docker run -d -p 8000:8000 -v ise-dados:/dados -e ISE_SECRET_KEY=... -e ISE_HTTPS=1 ise
```

Coloque o site atrás de HTTPS.

## 3. Trazer os dados que estão no Claude

1. No site atual, dentro do Claude, entre como administrador e abra **Setores e perguntas → Baixar backup completo**.
2. Envie o arquivo `ISE_BACKUP_….json` para o servidor e rode:

```bash
python importar_backup.py ISE_BACKUP_2026-10-22_1353.json
```

- O script confere o SHA-256 de cada registro. Se algum não conferir, nada é gravado.
- Registros que já existem são mantidos. Para restaurar um backup por cima dos dados, use `--substituir`. É uma decisão do administrador e fica na auditoria.
- O mesmo script restaura os backups automáticos do Drive.

## 4. Variáveis de ambiente

| Variável | Para quê |
|---|---|
| `ISE_SECRET_KEY` | obrigatória; protege as sessões de login |
| `ISE_BANCO` | caminho do banco (padrão `servidor/dados/ise.sqlite3`; no Docker, `/dados/ise.sqlite3`) |
| `ISE_HTTPS` | `1` quando o site estiver em HTTPS |
| `ANTHROPIC_API_KEY` | chave da API do Claude (https://console.anthropic.com). Sem ela, o site funciona sem a análise do Claude |
| `ISE_MODELO` | modelo do Claude; padrão `claude-opus-5-5` |
| `ISE_BACKUP_AUTOMATICO` | `1` liga o backup diário às 23h |
| `ISE_DRIVE_CREDENCIAL` | conteúdo (ou caminho) do arquivo JSON da conta de serviço do Google |
| `ISE_DRIVE_PASTA` | id da pasta "ISE — BACKUP SEGURANÇA DO TRABALHO" (o trecho final do link da pasta) |
| `ISE_RETENCAO_DIARIA_DIAS` / `ISE_RETENCAO_MENSAL_MESES` | retenção; padrão 30 dias e 12 meses (diretriz 3.5, ainda a validar pela empresa) |

Nunca coloque chaves no código nem no GitHub.

## 5. Backup automático no Google Drive

1. No Google Cloud Console (https://console.cloud.google.com):
   - crie um projeto;
   - ative a **Google Drive API**;
   - em **IAM → Contas de serviço**, crie uma conta;
   - em **Chaves**, gere uma chave JSON.
2. Crie um **Drive compartilhado** da empresa e coloque nele a pasta "ISE — BACKUP SEGURANÇA DO TRABALHO". Contas de serviço não gravam no "Meu Drive".
3. Adicione o e-mail da conta de serviço (termina em `iam.gserviceaccount.com`) ao Drive compartilhado como **Gerente de conteúdo**.
4. Preencha as variáveis:
   - `ISE_DRIVE_CREDENCIAL` com o conteúdo do JSON;
   - `ISE_DRIVE_PASTA` com o id da pasta;
   - `ISE_BACKUP_AUTOMATICO=1`.
5. Em **Usuários e backup**, clique em **Executar backup agora** e confira o resultado na tabela.

O que cada execução faz:
- grava `ISE_BACKUP_AAAA-MM-DD_2300.json` em **01 — Backup Diário**. É uma cópia completa e restaurável;
- envia as fotos novas para **03** e as assinaturas novas para **07**;
- no primeiro backup de cada mês, faz uma cópia em **02 — Backup Mensal**;
- grava o registro da execução em **09 — Registros de Backup**.

Cada arquivo é conferido pelo MD5 que o próprio Drive calcula. Em caso de diferença, o envio é repetido até 3 vezes. O resultado aparece:
- na tabela da página **Usuários e backup**;
- no painel **Setores e perguntas**;
- na central de alertas do administrador.

Em caso de falha:
- o arquivo com problema é renomeado como `INCOMPLETO_`;
- a última cópia íntegra é mantida;
- a limpeza da retenção não roda.

Backups antigos vão para a lixeira do Drive e não são apagados de vez. Fotos e assinaturas nunca são apagadas.

Rode o servidor com **um único processo** (`gunicorn -w 1 --threads 8`), como no Dockerfile. O agendador fica dentro dele, e vários processos repetiriam o backup. Para usar o cron do sistema em vez do agendador interno, deixe `ISE_BACKUP_AUTOMATICO` vazio e agende `python backup_drive.py`.

## Arquivos

- `app.py`: servidor, contas, permissões e API usada pelo site
- `static/plataforma.js`: ponte no navegador. Faz o site usar o servidor no lugar da plataforma do Claude, sem mudar o `index.html`
- `banco.py`: banco SQLite
- `ia.py`: análise do Claude
- `backup_drive.py`: backup no Google Drive
- `importar_backup.py`: importação e restauração
