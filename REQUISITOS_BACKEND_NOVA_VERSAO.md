# Requisitos — Back-end da nova versão

## 1. Decisão de arquitetura

Manter o backend em **Python** é adequado. Recomenda-se usar **FastAPI**, com **SQLAlchemy 2** (ou SQLModel) para persistência, **Alembic** para migrações e PostgreSQL em produção. FastAPI fornece uma API tipada, validação de requisições com Pydantic e documentação interativa automática, enquanto React consome a API de forma independente.

O código C enviado por alunos é **não confiável**. Portanto, a nova versão **não deve carregar bibliotecas enviadas por alunos com `ctypes`**. A versão antiga faz isso em `projetomd/mdproject/views.py` após chamar `gcc`; uma biblioteca maliciosa poderia executar no mesmo processo e com as permissões da API.

Em vez disso, cada entrega deve ser compilada e executada por um *worker* separado, dentro de um contêiner/ambiente isolado e descartável. Não é necessária uma biblioteca Python para “rodar C”: o backend chama o compilador e o executável por `subprocess`; a proteção vem do isolamento do ambiente de execução, não de `ctypes`.

```text
React ──HTTPS/JSON──> FastAPI ──> PostgreSQL / armazenamento de .zip
                            │
                            └──> fila de tarefas ──> worker isolado
                                                      ├─ valida .zip
                                                      ├─ gcc (container sem rede)
                                                      └─ executa encoder/decoder com limites
```

## 2. Componentes

- **API**: FastAPI, Pydantic para esquemas/validação e Uvicorn ou Gunicorn com workers Uvicorn para servir a aplicação.
- **Persistência**: SQLAlchemy 2 (ou SQLModel) e Alembic para migrações de banco.
- **Autenticação**: tokens de acesso de curta duração, preferencialmente em cookie `HttpOnly`, `Secure` e `SameSite`; implementar proteção CSRF quando a autenticação usar cookies.
- **Banco**: SQLite com modo WAL (`journal_mode=WAL`) e `foreign_keys=ON` para simplificar a execução e reduzir o consumo de memória, mantendo persistência em volume `/data`. Suporte a PostgreSQL preservado via configuração de `DATABASE_URL`.
- **Arquivos**: armazenamento privado (volume local em desenvolvimento; S3/compatível em produção). O `.zip` não pode ter URL pública.
- **Tarefas assíncronas**: Celery + Redis. Compilação e testes não podem bloquear uma requisição HTTP.
- **Executor**: imagem Docker específica com GCC e dependências mínimas. Para maior isolamento, usar Docker rootless com políticas restritivas, gVisor, Firecracker ou serviço de execução dedicado.
- **Observabilidade**: logs estruturados, auditoria de ações, métricas das tarefas e alertas de falha.

## 3. Modelo de domínio mínimo

| Entidade | Campos essenciais |
| --- | --- |
| Usuário | nome, e-mail/matrícula, senha/SSO, papel (`professor`, `aluno`, `administrador`) |
| Turma | nome, período, professor(es) |
| Grupo | turma, nome, membros |
| Atividade | turma, título, instruções, prazo, status, limites, política de feedback |
| Pareamento | atividade, grupo codificador, grupo decodificador |
| Entrega | atividade, grupo, papel, versão, arquivo, hash SHA-256, enviada_em, status |
| Build | entrega, compilador/versão, saída, status, duração |
| Caso de teste | atividade, identificador, visibilidade (`público`/`privado`), mensagem, par de primos, número de dígitos dos primos, chave de teste, ordem e ativo |
| Execução/Teste | pareamento, versões do encoder/decoder, total de casos, aprovados, reprovados, com erro, status, duração e saída sanitizada |
| Resultado de caso | execução, caso de teste, status, duração, diagnóstico sanitizado e dados permitidos pela política de feedback |
| Auditoria | ator, ação, recurso, data/hora, metadados |

Restrições importantes:

- um aluno pertence a no máximo um grupo por atividade;
- cada grupo possui somente uma entrega ativa por atividade e papel, mantendo o histórico das versões;
- cada pareamento tem exatamente um grupo codificador e um decodificador distintos;
- somente o professor da turma (ou administrador) pode criar pareamentos, ver todos os arquivos e disparar testes.

## 4. Contrato dos projetos C

O contrato deve abandonar leitura/gravação em arquivos fixos como `textEncript.txt`: arquivos com nomes globais causam colisão entre grupos e tornam o teste difícil de isolar.

Cada `.zip` deve conter somente fontes C e metadados permitidos, sem binários. Nome obrigatório conforme o papel:

| Papel | Arquivo obrigatório | Comando produzido pelo executor |
| --- | --- | --- |
| Codificador | `encoder.c` | `./encoder` |
| Decodificador | `decoder.c` | `./decoder` |

O executor compila com uma lista fixa de argumentos, por exemplo `gcc -std=c11 -Wall -Wextra -Werror -O2 arquivo.c -o programa`. Não aceitar `Makefile`, *scripts*, opções de compilação ou bibliotecas arbitrárias na primeira versão.

### 4.1. Protocolo de entrada e saída

Usar `stdin` e `stdout`, sempre em UTF-8, com um formato estável. Uma opção simples é JSON por linha:

**Entrada do codificador**

```json
{"message":"texto de teste","public_key":{"n":3233,"e":17}}
```

**Saída esperada do codificador**

```json
{"ciphertext":[2790,2182, ...]}
```

**Entrada do decodificador**

```json
{"ciphertext":[2790,2182],"private_key":{"n":3233,"d":2753}}
```

**Saída esperada do decodificador**

```json
{"message":"texto de teste"}
```

O professor define ou o sistema gera chaves de teste adequadas. O enunciado deve informar se a atividade é RSA didático por caractere e seus limites; não apresentar esse algoritmo como criptografia segura de produção.

Para suportar pares de primos de até nove dígitos, a implementação C deve realizar multiplicação modular sem *overflow*. Embora `n = p × q` caiba em `int64_t` (aproximadamente até `10^18`), o produto intermediário de uma exponenciação modular pode ultrapassar `long long`. O contrato deve exigir uma rotina `mul_mod` segura — por soma/dobro modular em C99 ou por `__int128` no compilador GCC do executor — e seu uso em `mod_pow`.

O backend deve disponibilizar um repositório-modelo com `encoder.c`/`decoder.c`, documentação do protocolo e casos públicos. Atualizar ou substituir `requisitosC.md` quando esse contrato for aprovado.

## 5. Bateria de testes de criptografia e descriptografia

Os testes das entregas dos alunos são **testes de integração/caixa-preta**, e não apenas testes unitários: para cada caso, o executor fornece uma mensagem ao `encoder`, envia o `ciphertext` produzido ao `decoder` e compara a mensagem final com a mensagem original. O resultado de um caso só é aprovado se ambos os programas obedecerem ao protocolo e a comparação for exata.

### 5.1. Matriz obrigatória: 240 casos

A bateria obrigatória de cada execução tem **240 casos**: as mesmas **30 mensagens** são executadas com oito pares de chaves RSA, em que **ambos os primos `p` e `q`** possuem, respectivamente, 2, 3, 4, 5, 6, 7, 8 e 9 dígitos.

| Faixa de primos (`p` e `q`) | Mensagens testadas | Quantidade |
| --- | --- | --- |
| 2 dígitos | textos 01 a 30 | 30 |
| 3 dígitos | textos 01 a 30 | 30 |
| 4 dígitos | textos 01 a 30 | 30 |
| 5 dígitos | textos 01 a 30 | 30 |
| 6 dígitos | textos 01 a 30 | 30 |
| 7 dígitos | textos 01 a 30 | 30 |
| 8 dígitos | textos 01 a 30 | 30 |
| 9 dígitos | textos 01 a 30 | 30 |
| **Total** | **30 textos × 8 faixas** | **240** |

- Os 30 textos têm complexidade crescente: textos 01–10 são curtos (1–20 caracteres), 11–20 são médios (21–200 caracteres) e 21–30 são longos (201–1.000 caracteres). Dentro de cada faixa, o tamanho deve crescer ou permanecer igual.
- As mensagens usam somente ASCII suportado e cobrem letras, dígitos, espaços, pontuação e caracteres-limite definidos no enunciado. O conjunto exato é definido e versionado pelo servidor.
- Para cada faixa, o servidor escolhe primos distintos válidos com a quantidade exigida de dígitos, garantindo `n = p × q > 255`, gera `n`, `e` e `d` válidos e fornece ao encoder/decoder apenas as chaves previstas no protocolo. A chave nunca é aceita como entrada confiável da entrega.
- Os 240 casos são obrigatórios para a atividade; a publicação só é permitida quando a matriz estiver completa. Uma alteração de texto, primo ou chave cria nova versão da bateria para preservar a reprodutibilidade de execuções anteriores.
- Casos públicos mostram entrada, status e diagnóstico; casos privados mostram apenas identificador/status e não podem revelar mensagem, chave ou saída esperada.
- Toda execução registra as versões exatas de `encoder.c` e `decoder.c`, versão da bateria, faixa de dígitos dos primos e resultado individual de cada caso.
- Ao receber um novo upload, o sistema compila a entrega. Se a entrega complementar ativa já estiver pronta, executa a bateria automaticamente; caso contrário, informa o estado `aguardando entrega complementar`. O professor pode reexecutar a bateria depois.

O resumo retornado pela API deve conter, no mínimo, `total`, `approved`, `failed`, `errored`, `score_percent`, o estado geral e um subtotal por faixa de dígitos dos primos. Exemplo: `222 de 240 testes aprovados (92,5%)`; `2 dígitos: 30/30`, `3 dígitos: 29/30`, ..., `9 dígitos: 22/30`.

### 5.2. Teste personalizado do aluno

Além da bateria oficial, o dono de um envio pode solicitar um teste isolado, informando `p`, `q`, `e` e uma mensagem. A API valida que `p` e `q` são primos distintos de 2 a 9 dígitos, que `1 < e < phi(n)` e que `mdc(e, phi(n)) = 1`; em seguida calcula `n` e `d` internamente. A mensagem possui de 1 a 1.000 caracteres ASCII imprimíveis e não pode conter aspas duplas ou barra invertida, pois essa é a limitação do protocolo didático de referência.

O endpoint `POST /api/v1/rsa-uploads/{upload_id}/custom-test` executa `encoder.c` e `decoder.c` do ZIP do próprio aluno e devolve `status`, chave pública, `ciphertext`, mensagem decifrada e diagnóstico. Esse resultado não altera a nota nem substitui a bateria oficial.

Além dos testes das entregas, o próprio backend deve possuir testes unitários para validação de ZIP, autorização, criação de casos, cálculo de resumo e serialização de respostas.

## 6. Upload, validação e ciclo de execução

1. A API confirma a identidade do aluno, sua turma, grupo, papel e prazo.
2. Aceita `multipart/form-data` com um `.zip` dentro do limite configurado (sugestão inicial: 1 MB compactado e 5 MB extraído).
3. Valida tipo/assinatura do ZIP, número de arquivos, profundidade de diretórios, tamanho extraído e bloqueia caminhos absolutos ou `..` (*Zip Slip*) e links simbólicos.
4. Confere que existe exatamente o arquivo obrigatório no nome/caminho permitido e que todos os demais arquivos são permitidos.
5. Armazena a versão, calcula SHA-256 e cria uma tarefa de build.
6. O worker extrai em diretório temporário exclusivo, compila e registra stdout/stderr limitado.
7. Se ambos os projetos do pareamento compilarem, o worker executa todos os casos da bateria: mensagem → `encoder` → `decoder` → comparação com a mensagem original.
8. A API persiste o resultado de cada caso, calcula o resumo (`aprovados/total`) e publica o resultado; arquivos temporários e executáveis são removidos ao término.

Estados sugeridos para entrega: `received`, `validating`, `queued`, `building`, `build_failed`, `ready`, `testing`, `passed`, `failed`, `rejected`, `expired`.

## 7. Segurança obrigatória do executor

- Sem rede, sem privilégios de root, sem montagem de diretórios do host e com sistema de arquivos somente leitura, exceto um diretório temporário vazio.
- Limites por execução configuráveis: tempo de CPU/parede (ex.: 2 s), memória (ex.: 64 MB), tamanho de stdout/stderr (ex.: 64 KB), processos (1 ou poucos), número de arquivos e espaço em disco.
- Aplicar *timeout* externo e encerrar o processo/grupo de processos ao excedê-lo.
- Usar *cgroups* e/ou `ulimit`/`resource.setrlimit`; aplicar perfil seccomp/AppArmor/SELinux quando disponível.
- Sanitizar logs antes de enviá-los à API; não incluir caminhos do host, tokens, variáveis de ambiente ou detalhes da infraestrutura.
- Executar somente comandos com argumentos construídos pelo servidor; nunca usar `shell=True` nem comandos fornecidos pelo arquivo enviado.
- Gerar chaves e casos de teste no backend; não confiar nas chaves ou resultados declarados pelo programa do aluno.

`ctypes` pode permanecer apenas para bibliotecas C internas, revisadas e distribuídas pela equipe — jamais para artefatos enviados pelos alunos.

## 8. API mínima

| Método e rota | Ação | Autorização |
| --- | --- | --- |
| `GET /api/v1/me` | perfil e permissões atuais | autenticado |
| `GET, POST /api/v1/rsa-uploads` | listar/enviar ZIP individual | aluno dono do envio |
| `POST /api/v1/rsa-uploads/{id}/test` | iniciar bateria oficial de 240 casos | aluno dono do envio |
| `POST /api/v1/rsa-uploads/{id}/custom-test` | executar uma cifra/decifra com `p`, `q`, `e` e mensagem escolhidos | aluno dono do envio |
| `GET, POST /api/v1/activities` | listar/criar atividades | autenticado/professor |
| `GET, PATCH /api/v1/activities/{id}` | consultar/editar atividade | membro/professor |
| `GET, POST /api/v1/activities/{id}/groups` | listar/criar grupos | membro/professor |
| `POST /api/v1/activities/{id}/pairings` | definir grupos encoder/decoder | professor |
| `POST /api/v1/activities/{id}/submissions` | enviar `.zip` | aluno do grupo correto |
| `GET /api/v1/submissions/{id}` | status e feedback permitido | dono/professor |
| `GET /api/v1/submissions/{id}/logs` | logs sanitizados | dono/professor, por política |
| `POST /api/v1/activities/{id}/runs` | executar/reexecutar testes | professor |
| `GET /api/v1/runs/{id}` | resultado de teste integrado | membro/professor |
| `GET /api/v1/runs/{id}/test-results` | resumo e resultados dos casos visíveis | membro/professor |

Todas as rotas devem validar objeto por objeto: não basta validar o papel geral do usuário.

## 9. Requisitos não funcionais

- Senhas com hash seguro e proteção contra força bruta; HTTPS obrigatório em produção.
- Proteção CSRF para autenticação por cookie, CORS restrito para front-end em domínio separado e limite de requisições/upload.
- Banco com migrações, backups e retenção definida para arquivos e logs.
- Testes automatizados para permissões, ZIP malicioso, erros de compilação, timeout, saída excessiva e integração encoder/decoder.
- Ambiente local reproduzível com Docker Compose (API, banco, Redis e executor), sem executar C de aluno diretamente na máquina do desenvolvedor.
- Política LGPD definida: finalidade, dados coletados, responsáveis, tempo de retenção e exclusão/anonimização de entregas.

## 10. Critérios de aceite do backend

- Um professor cria uma atividade, associa dois grupos e os alunos corretos enviam suas versões.
- O sistema rejeita ZIP inválido/malicioso antes da compilação e registra o motivo.
- Um programa em loop infinito, que tenta acessar a rede ou excede memória não afeta a API nem outras tarefas.
- Uma execução informa corretamente o total de 240, a quantidade aprovada, a percentagem e os oito subtotais de 30 casos, inclusive quando há falha de compilação ou timeout.
- O resultado só é `passed` quando a saída do decoder corresponde exatamente à mensagem de entrada nos 240 casos obrigatórios.
- Casos privados não expõem mensagem, chave, ciphertext ou saída esperada ao aluno.
- Reenvios preservam histórico e não misturam arquivos, logs ou resultados de grupos diferentes.
- Nenhum `.so`/`.dll` de aluno é carregado pelo processo FastAPI.

## 11. Fases sugeridas

1. **Fundação**: API FastAPI, autenticação, modelos SQLAlchemy, migrações Alembic, turmas, grupos, atividades e React com login/painéis.
2. **Entrega segura**: upload versionado, validação de ZIP, armazenamento privado e histórico.
3. **Executor**: fila, container restrito, compilação e logs sanitizados.
4. **Avaliação integrada**: pareamento encoder/decoder, casos públicos/privados e feedback.
5. **Operação**: auditoria, observabilidade, testes de segurança, backup e deploy.
