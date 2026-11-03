# RSA Project - Backend API

API em FastAPI para gerenciamento de usuários, turmas, grupos e execução automatizada de testes de interoperabilidade RSA (algoritmos em C).

## Tecnologias
- **Linguagem**: Python 3.12
- **Framework**: FastAPI + Uvicorn
- **ORM / Banco de Dados**: SQLAlchemy 2 + SQLite (WAL mode e Foreign Keys ativados)
- **Autenticação**: JWT com hash de senhas via Argon2 (pwdlib)
- **Containerização**: Docker & Docker Compose

## Estrutura do Projeto
```
RSA-Backend/
├── app/
│   ├── auth.py         # Autenticação JWT e senhas
│   ├── config.py       # Configurações e variáveis de ambiente
│   ├── database.py     # Conexão SQLite e sessão SQLAlchemy
│   ├── main.py         # Rotas e ponto de entrada da API
│   ├── models.py       # Modelos do banco de dados
│   ├── schemas.py      # Esquemas Pydantic
│   └── testing.py      # Bateria de testes RSA e runner de arquivos C
├── tests/              # Testes unitários do backend
├── Dockerfile          # Imagem Docker de produção
├── docker-compose.yml  # Orquestração do serviço e volume de dados
├── requirements.txt    # Dependências Python
└── .env.example        # Modelo de variáveis de ambiente
```

## Como Rodar

### 1. Via Docker Compose (Recomendado para Produção / VM)

1. Crie o arquivo `.env`:
   ```bash
   cp .env.example .env
   ```
2. Ajuste o `SECRET_KEY` e adicione a URL do seu frontend na Vercel em `CORS_ORIGINS`.
3. Inicie o serviço:
   ```bash
   docker compose up -d --build
   ```

A API estará acessível em `http://localhost:8000` (docs em `/docs`).

### 2. Execução Local sem Docker

1. Crie e ative um ambiente virtual:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```
2. Instale as dependências:
   ```bash
   pip install -r requirements.txt
   ```
3. Execute o servidor:
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   ```

### 3. Rodar Testes
```bash
pytest
```
