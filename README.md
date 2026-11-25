# 🛒 Mercadinho Seu João

Sistema web de **controle de estoque** para o Mercadinho Seu João: cadastro de produtos, entradas e saídas, controle de validade por lote, alertas e relatórios, com **níveis de usuário e permissões por tela**.

![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-backend-000000?logo=flask&logoColor=white)
![Supabase](https://img.shields.io/badge/Supabase-PostgreSQL-3ECF8E?logo=supabase&logoColor=white)

---

## ✨ Funcionalidades

- **Painel** com visão geral do estoque e alertas
- **Produtos** e categorias
- **Entradas** e **Saídas** de estoque, com controle por **lote e validade**
- Baixa automática pela regra **FEFO** (sai primeiro o lote com validade mais próxima)
- **Alertas** de estoque baixo e validade próxima
- **Relatórios**
- **Fornecedores**
- **Caixa**
- **Usuários e permissões** (somente o Dono)

## 🔐 Níveis de usuário e permissões

| Perfil | Acesso |
|---|---|
| **DONO** (super admin) | Acessa todas as telas, cria e edita usuários, faz ajustes de estoque e exclui movimentações |
| **FUNCIONARIO** | Acessa apenas as telas liberadas pelo Dono para ele |

As telas que podem ser liberadas para um funcionário são: `painel`, `caixa`, `produtos`, `entradas`, `saidas`, `relatorios` e `fornecedores`.

Como funciona:

- O login gera um **token assinado**, válido por 8 horas, que o front envia em todas as chamadas à API.
- As permissões são verificadas **no servidor**: um funcionário recebe erro 403 se chamar uma rota que não pode, mesmo digitando o endereço direto.
- O menu lateral mostra só as telas permitidas, e cada página redireciona quem não tem acesso.
- As senhas são salvas com **hash**.
- Usuários são **inativados**, nunca excluídos.

## 🧰 Tecnologias

- **Front-end:** HTML, CSS e JavaScript
- **Back-end:** Python + Flask
- **Banco de dados:** Supabase (PostgreSQL), compartilhado pela equipe

## 📁 Estrutura do projeto

```
mercadinho/
├── app.py               # Servidor Flask (rotas, autenticação, regras de estoque)
├── conexao.py           # Conexão com o Supabase
├── requirements.txt     # Dependências Python
├── *.html               # Páginas (painel, produtos, entradas, saídas, etc.)
├── main.js              # Script principal do front-end
├── css/                 # Estilos
├── js/                  # Lógica de cada página e controle de sessão
├── services/api/        # Cliente de API (envio do token, tratamento de erros)
├── assets/              # Imagens e ícones
├── tests/               # Testes
└── CLAUDE.md            # Contexto e convenções do projeto
```

---

## 🚀 Preparando o ambiente

O banco de dados (Supabase) **já está configurado e é compartilhado por toda a equipe**. Você só precisa preparar a sua máquina e conectar ao mesmo banco.

### Pré-requisitos

- [Python 3.12 ou superior](https://www.python.org/downloads/) (marque a opção **Add Python to PATH** na instalação)
- [Git](https://git-scm.com/)
- Um editor de código, como o [VS Code](https://code.visualstudio.com/)

### 1. Clonar o repositório

```bash
git clone https://github.com/olavomenezesdev/mercadinho.git
cd mercadinho
```

### 2. Criar e ativar o ambiente virtual (`.venv`)

O ambiente virtual isola as bibliotecas do projeto. Você cria uma vez e **ativa toda vez que for rodar o projeto**.

**Windows (PowerShell):**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

> Se o PowerShell bloquear a ativação, rode antes (vale só para essa janela):
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned`

**Linux / macOS:**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Quando o ambiente estiver ativo, o terminal mostra **`(.venv)`** no começo da linha.

### 3. Instalar as dependências

Com o `.venv` ativo:

```bash
pip install -r requirements.txt
```

### 4. Configurar o arquivo `.env`

O `.env` guarda os dados de conexão com o banco e **não vai para o GitHub**. Crie o arquivo na raiz do projeto (ao lado do `app.py`) copiando o modelo:

**Windows (PowerShell):**

```powershell
Copy-Item .env.example .env
```

**Linux / macOS:**

```bash
cp .env.example .env
```

Depois abra o `.env` e preencha:

```env
SUPABASE_URL=https://SEU-PROJETO.supabase.co
SUPABASE_KEY=sua-chave-do-supabase
SECRET_KEY=um-texto-longo-e-aleatorio
```

| Variável | De onde vem |
|---|---|
| `SUPABASE_URL` | **Peça ao responsável pelo projeto** (Olavo, Bruno, Nicholas e Higor). É o mesmo valor para toda a equipe. |
| `SUPABASE_KEY` | **Peça ao responsável pelo projeto.** É a mesma para toda a equipe. Envie e receba por mensagem privada, nunca em grupo público ou no GitHub. |
| `SECRET_KEY` | Cada pessoa gera a sua. Ela assina os tokens de login na sua máquina. |

Para gerar a sua `SECRET_KEY`, rode:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Copie o texto que aparecer e cole no `.env` depois de `SECRET_KEY=`.

> ⚠️ **Nunca envie o `.env` para o GitHub** nem compartilhe a chave do Supabase fora da equipe.

### 5. Iniciar o servidor

Com o `.venv` ativo:

```bash
python app.py
```

Quando aparecer `Running on http://127.0.0.1:8000`, abra no navegador:

**http://127.0.0.1:8000**

Deixe esse terminal aberto enquanto usa o sistema. Para parar, use `Ctrl + C`. **Sempre que alterar o `app.py`, pare e inicie de novo.** Para testes de API (curl, etc.), abra um segundo terminal.

### 6. Entrar no sistema

Use um dos usuários já cadastrados no banco (peça o login ao responsável pelo projeto):

- **Dono:** vê todas as telas, inclusive **Usuários**.
- **Funcionário:** vê apenas as telas liberadas para ele.

Se precisar de um novo usuário, entre como Dono e crie pela tela **Usuários**, escolhendo quais telas ele pode acessar.

---

## 👥 Trabalhando em equipe

Como todos usam o **mesmo banco**, tenha cuidado: o que você cadastra, altera ou apaga aparece para todo mundo.

- **Não apague nem altere dados reais** do banco sem avisar a equipe.
- **Não rode a suíte de testes completa** contra o banco compartilhado: ela grava registros de teste nele.
- Para testar sem tocar no banco, rode `python -m unittest tests.test_estoque tests.test_app.AuthenticationTests`. Esses testes usam um banco em memória (`tests/fake_supabase.py`).
- Antes de começar a trabalhar, atualize o seu código:

  ```bash
  git checkout main
  git pull
  ```

- Trabalhe em uma **branch própria** e abra um Pull Request para a `main`:

  ```bash
  git checkout -b feat/nome-da-alteracao
  git add .
  git commit -m "feat: descreva o que mudou"
  git push -u origin feat/nome-da-alteracao
  ```

- Se alguém trouxer novas bibliotecas, rode de novo `pip install -r requirements.txt` depois do `git pull`.
- Leia o `CLAUDE.md`: ele traz as regras de negócio e as convenções do projeto.

## 🛠️ Problemas comuns

| Problema | Solução |
|---|---|
| `python` não é reconhecido | Reinstale o Python marcando **Add Python to PATH**, ou tente `py` no lugar de `python` |
| O PowerShell não ativa o `.venv` | Rode `Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned` e tente de novo |
| `ModuleNotFoundError` ao rodar | O `.venv` não está ativo (falta o `(.venv)` no terminal) ou faltou o `pip install -r requirements.txt` |
| O servidor não sobe ou dá erro de variável | Confira se o `.env` está na raiz e tem as três variáveis preenchidas |
| `Failed to connect to 127.0.0.1 port 8000` | O servidor não está rodando; inicie com `python app.py` e deixe o terminal aberto |
| Telas sem dados ou erro de acesso | Saia, entre de novo e recarregue com `Ctrl + F5` |
| `Credenciais inválidas` | Confira o login e a senha com o responsável pelo projeto; o usuário também precisa estar ativo |
| `Sem permissão` ao abrir uma tela | O Dono precisa liberar a tela para esse funcionário, na página **Usuários** |
| Alterei o código e nada mudou | Pare o servidor (`Ctrl + C`) e inicie de novo; depois recarregue o navegador com `Ctrl + F5` |

## 👨‍💻 Autores

Projeto acadêmico de Sistemas de Informação, desenvolvido por **Bruno Onishi, Olavo Menezes, Higor Rodrigues e Nicholas Cardoso**.
