# Servidor Python do Sistema de Inspeção de Segurança (ISE)

Este servidor roda o mesmo site do repositório (`index.html`) fora do Claude. Ele substitui o que a plataforma do Claude oferecia:

| Recurso | Dentro do Claude | Neste servidor |
|---|---|---|
| Banco compartilhado | banco da plataforma | PostgreSQL (`DATABASE_URL`, ex.: Neon gratuito) ou SQLite, com sincronização a cada 4 segundos entre os aparelhos |
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
flask --app app criar-admin        # pede e-mail, nome e senha do administrador (ou use ISE_ADMIN_*)
flask --app app run                # abra http://127.0.0.1:5000
```

## 2. Publicar sem custo a partir do GitHub (Render gratuito + Neon gratuito)

O plano gratuito do Render não guarda arquivos: os dados ficam num PostgreSQL gratuito do **Neon**, que não expira. O servidor gratuito dorme depois de 15 minutos sem uso e leva cerca de um minuto para acordar no primeiro acesso.

**a) Banco gratuito (Neon)**
1. Crie uma conta em https://neon.tech (pode entrar com a conta do GitHub) e clique em **Create project**. Nome: `inspecao-seguranca`; região: **AWS US East (N. Virginia)**, a mesma região do site no Render (o Render não tem servidores na América do Sul).
2. No painel do projeto, clique em **Connect** e copie a *connection string* (começa com `postgresql://` e termina com `sslmode=require`). Ela é a `DATABASE_URL`.

**b) Site no Render**
1. Em https://dashboard.render.com, escolha **New → Blueprint** e selecione o repositório `INSPE-O-DE-SEGURAN-A`. O `render.yaml` já escolhe o plano **Free**.
2. Preencha as variáveis pedidas:
   - `DATABASE_URL`: a connection string do Neon;
   - `ISE_ADMIN_EMAIL`, `ISE_ADMIN_NOME`, `ISE_ADMIN_SENHA`: a sua conta de administrador (senha com pelo menos 8 caracteres), criada sozinha na primeira vez que o site sobe;
   - `ANTHROPIC_API_KEY`, `ISE_DRIVE_CREDENCIAL`, `ISE_DRIVE_PASTA`: podem ficar em branco agora.
3. Clique em **Apply**. Quando o serviço ficar *Live*, abra o endereço `https://….onrender.com`, entre com a conta de administrador e troque a senha em **Senha**.
4. Cada `git push` na branch principal publica a nova versão sozinho.

**c) Backup diário mesmo com o servidor dormindo**
O GitHub Actions (gratuito) chama o servidor todo dia às 23h e dispara o backup (`.github/workflows/backup-diario.yml`).
1. No Render, abra o serviço → **Environment** e copie o valor de `ISE_BACKUP_TOKEN` (gerado sozinho).
2. No GitHub, abra o repositório → **Settings → Secrets and variables → Actions → New repository secret** e crie:
   - `ISE_URL`: o endereço do site, por exemplo `https://inspecao-seguranca.onrender.com`;
   - `ISE_BACKUP_TOKEN`: o valor copiado do Render.
3. Em **Actions → Backup diário do ISE → Run workflow** dá para testar na hora.

O GitHub desliga agendamentos de repositórios sem nenhuma alteração por 60 dias e avisa por e-mail; basta reativar em **Actions**. O backup só envia arquivos depois de configurado o Google Drive (seção 5).

**Limites do gratuito:** o Neon oferece 0,5 GB de banco (milhares de inspeções com fotos comprimidas) e uma cota mensal de horas de processamento, gasta só enquanto o site está em uso; o Render oferece 750 horas por mês, suficiente para um serviço no ar o mês todo. O site só consulta o servidor com a aba visível, para que uma aba esquecida aberta não gaste essas cotas. A análise do Claude é paga à parte pela API e é opcional: sem `ANTHROPIC_API_KEY`, o site funciona normalmente, só sem a análise.

**Outra opção:** qualquer servidor com Docker:

```bash
docker build -t ise .
docker run -d -p 8000:8000 -e DATABASE_URL=... -e ISE_SECRET_KEY=... -e ISE_HTTPS=1 ise
```

Sem `DATABASE_URL`, o servidor usa SQLite em `/dados`; nesse caso monte um volume (`-v ise-dados:/dados`) e use `ISE_BACKUP_AUTOMATICO=1`, pois o servidor não dorme.

## 3. Trazer os dados que estão no Claude

1. No site atual, dentro do Claude, entre como administrador e abra **Setores e perguntas → Baixar backup completo**.
2. No novo site, abra **Usuários e backup → Importar ou restaurar backup**, escolha o arquivo e clique em **Importar**. (No computador, o mesmo pode ser feito com `python importar_backup.py ISE_BACKUP_….json`.)

- O script confere o SHA-256 de cada registro. Se algum não conferir, nada é gravado.
- Registros que já existem são mantidos. Para restaurar um backup por cima dos dados, use `--substituir`. É uma decisão do administrador e fica na auditoria.
- O mesmo script restaura os backups automáticos do Drive.

## 4. Variáveis de ambiente

| Variável | Para quê |
|---|---|
| `ISE_SECRET_KEY` | obrigatória; protege as sessões de login |
| `DATABASE_URL` | PostgreSQL (Neon gratuito). Sem ela, usa SQLite |
| `ISE_BANCO` | caminho do SQLite (padrão `servidor/dados/ise.sqlite3`; no Docker, `/dados/ise.sqlite3`) |
| `ISE_ADMIN_EMAIL` / `ISE_ADMIN_NOME` / `ISE_ADMIN_SENHA` | criam o administrador na primeira vez que o servidor sobe (depois disso não mudam nada) |
| `ISE_BACKUP_TOKEN` | senha que o GitHub Actions usa para disparar o backup das 23h |
| `ISE_HTTPS` | `1` quando o site estiver em HTTPS |
| `ANTHROPIC_API_KEY` | chave da API do Claude (https://console.anthropic.com). Sem ela, o site funciona sem a análise do Claude |
| `ISE_MODELO` | modelo do Claude; padrão `claude-opus-5-5` |
| `ISE_BACKUP_AUTOMATICO` | `1` faz o próprio servidor agendar o backup das 23h (só para servidor que não dorme) |
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
   - no Render gratuito, os segredos do GitHub Actions da seção 2c; em servidor sempre ligado, `ISE_BACKUP_AUTOMATICO=1`.
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

Rode o servidor com **um único processo** (`gunicorn -w 1 --threads 8`), como no Dockerfile: o agendador opcional fica dentro dele, e vários processos repetiriam o backup. Também dá para usar o cron do sistema (`python backup_drive.py`).

## Arquivos

- `app.py`: servidor, contas, permissões e API usada pelo site
- `static/plataforma.js`: ponte no navegador. Faz o site usar o servidor no lugar da plataforma do Claude, sem mudar o `index.html`
- `banco.py`: banco (PostgreSQL ou SQLite)
- `ia.py`: análise do Claude
- `backup_drive.py`: backup no Google Drive
- `importar_backup.py`: importação e restauração
