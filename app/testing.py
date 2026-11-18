"""Compilação e teste de entregas RSA em C (individuais, com oráculo ou entre grupos)."""

from __future__ import annotations

import json
import math
import os
import resource
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

MAX_LOG_BYTES = 64 * 1024
TIMEOUT_SECONDS = 3
MEMORY_BYTES = 128 * 1024 * 1024

ORACLE_DIR = Path(__file__).resolve().parent / "oracle"
ORACLE_ENCODER_SOURCE = ORACLE_DIR / "encoder.c"
ORACLE_DECODER_SOURCE = ORACLE_DIR / "decoder.c"
VALID_TEST_MODES = ("both", "encoder_only", "decoder_only")


class TestExecutionError(Exception):
    pass


@dataclass(frozen=True)
class TestCase:
    index: int
    prime_digits: int
    message: str
    n: int
    e: int
    d: int


def inspect_zip_sources(zip_path: Path) -> dict[str, bool]:
    """Inspect whether a submission ZIP has unique encoder.c and/or decoder.c files."""
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = [Path(info.filename).name for info in archive.infolist() if not info.is_dir()]
    except zipfile.BadZipFile as exc:
        raise TestExecutionError("O arquivo enviado não é um ZIP válido.") from exc

    encoder_count = names.count("encoder.c")
    decoder_count = names.count("decoder.c")
    if encoder_count > 1 or decoder_count > 1:
        raise TestExecutionError("O ZIP não pode conter arquivos encoder.c ou decoder.c duplicados.")
    if encoder_count == 0 and decoder_count == 0:
        raise TestExecutionError("O ZIP deve conter encoder.c, decoder.c ou ambos.")
    return {"has_encoder": encoder_count == 1, "has_decoder": decoder_count == 1}


def resolve_test_mode(zip_path: Path, requested_mode: str = "auto") -> str:
    """Resolve and validate which test mode ('both', 'encoder_only', 'decoder_only') to run."""
    sources = inspect_zip_sources(zip_path)
    has_encoder = sources["has_encoder"]
    has_decoder = sources["has_decoder"]

    if requested_mode == "auto":
        if has_encoder and has_decoder:
            return "both"
        return "encoder_only" if has_encoder else "decoder_only"

    if requested_mode not in VALID_TEST_MODES:
        raise TestExecutionError("Modo de teste inválido.")
    if requested_mode in ("both", "encoder_only") and not has_encoder:
        raise TestExecutionError("O ZIP enviado não contém o arquivo encoder.c.")
    if requested_mode in ("both", "decoder_only") and not has_decoder:
        raise TestExecutionError("O ZIP enviado não contém o arquivo decoder.c.")
    return requested_mode


def validate_test_sources(zip_path: Path, mode: str = "both") -> None:
    resolve_test_mode(zip_path, mode)


def extract_source(zip_path: Path, name: str, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        source_info = next(info for info in archive.infolist() if not info.is_dir() and Path(info.filename).name == name)
        source_path = destination / name
        with archive.open(source_info) as source, source_path.open("wb") as target:
            shutil.copyfileobj(source, target)
    return source_path


def compile_source(source: Path, executable: Path) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["gcc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2", str(source), "-o", str(executable)],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        return False, "GCC não está disponível no executor."
    except subprocess.TimeoutExpired:
        return False, "A compilação excedeu o tempo máximo."
    return result.returncode == 0, (result.stdout + result.stderr).strip()[:MAX_LOG_BYTES]


def _limit_resources() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (TIMEOUT_SECONDS, TIMEOUT_SECONDS + 1))
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_LOG_BYTES, MAX_LOG_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))


def normalize_output(output: str | bytes | None) -> str:
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")[:MAX_LOG_BYTES]
    return (output or "")[:MAX_LOG_BYTES]


def run_program(executable: Path, payload: dict) -> tuple[dict | None, str, str, str]:
    """Run a submission and return parsed JSON, diagnostic, raw stdout and raw stderr."""
    try:
        result = subprocess.run(
            [str(executable)],
            input=json.dumps(payload, separators=(",", ":")) + "\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECONDS,
            check=False,
            preexec_fn=_limit_resources if os.name != "nt" else None,
        )
    except subprocess.TimeoutExpired as exc:
        return None, "Tempo de execução excedido.", normalize_output(exc.stdout), normalize_output(exc.stderr)
    stdout = normalize_output(result.stdout)
    stderr = normalize_output(result.stderr)
    if result.returncode != 0:
        return None, f"Programa terminou com código {result.returncode}. {stderr}"[:MAX_LOG_BYTES], stdout, stderr
    lines = [line for line in stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        return None, "O programa deve imprimir exatamente uma linha JSON.", stdout, stderr
    try:
        return json.loads(lines[0]), "", stdout, stderr
    except json.JSONDecodeError:
        return None, "A saída do programa não é um JSON válido.", stdout, stderr


def is_prime(number: int) -> bool:
    if number < 2:
        return False
    if number % 2 == 0:
        return number == 2
    divisor = 3
    while divisor * divisor <= number:
        if number % divisor == 0:
            return False
        divisor += 2
    return True


def next_prime(number: int) -> int:
    while not is_prime(number):
        number += 1
    return number


def key_for_digits(digits: int) -> tuple[int, int, int]:
    lower = 10 ** (digits - 1)
    p = next_prime(max(17, lower + 3))
    q = next_prime(p + 2)
    phi = (p - 1) * (q - 1)
    e = next(candidate for candidate in (65537, 257, 17, 5, 3) if math.gcd(candidate, phi) == 1)
    return p * q, e, pow(e, -1, phi)


def validate_custom_test_input(message: str, p: int, q: int, e: int) -> tuple[int, int]:
    """Validate a manual RSA example and return its public modulus and private exponent."""
    if not message.isascii() or any(not 32 <= ord(character) <= 126 for character in message):
        raise ValueError("A mensagem deve usar apenas caracteres ASCII imprimiveis.")
    if '"' in message or "\\" in message:
        raise ValueError("A mensagem de teste nao pode conter aspas duplas nem barra invertida.")
    if p == q:
        raise ValueError("p e q devem ser primos distintos.")
    if not is_prime(p) or not is_prime(q):
        raise ValueError("p e q devem ser numeros primos.")

    phi = (p - 1) * (q - 1)
    if e >= phi or math.gcd(e, phi) != 1:
        raise ValueError("e deve ser menor que phi(n) e coprimo com phi(n).")
    return p * q, pow(e, -1, phi)


def _prepare_student_and_oracle_programs(zip_path: Path, mode: str, root: Path) -> tuple[Path | None, Path | None, str]:
    """Compile student submission paired with the menu oracle when testing a single side."""
    if mode in ("both", "encoder_only"):
        encoder_source = extract_source(zip_path, "encoder.c", root / "enc")
    else:
        if not ORACLE_ENCODER_SOURCE.exists():
            return None, None, "Oráculo encoder.c não encontrado no servidor."
        encoder_source = ORACLE_ENCODER_SOURCE

    if mode in ("both", "decoder_only"):
        decoder_source = extract_source(zip_path, "decoder.c", root / "dec")
    else:
        if not ORACLE_DECODER_SOURCE.exists():
            return None, None, "Oráculo decoder.c não encontrado no servidor."
        decoder_source = ORACLE_DECODER_SOURCE

    encoder = root / "encoder"
    decoder = root / "decoder"
    encoder_ok, encoder_log = compile_source(encoder_source, encoder)
    decoder_ok, decoder_log = compile_source(decoder_source, decoder)
    if not encoder_ok:
        label = "oráculo encoder.c" if mode == "decoder_only" else "encoder.c"
        return None, None, f"Falha de compilação ({label}): {encoder_log}"
    if not decoder_ok:
        label = "oráculo decoder.c" if mode == "encoder_only" else "decoder.c"
        return None, None, f"Falha de compilação ({label}): {decoder_log}"
    return encoder, decoder, ""


def _evaluate_single_case(
    encoder: Path,
    decoder: Path,
    message: str,
    n: int,
    e: int,
    d: int,
    mode: str = "both",
) -> dict:
    """Execute one RSA round trip and return outcome ('approved', 'failed', 'errored') and details."""
    encrypted, enc_error, _, _ = run_program(
        encoder,
        {"message": message, "public_key": {"n": n, "e": e}},
    )
    ciphertext = encrypted.get("ciphertext") if isinstance(encrypted, dict) else None
    if not isinstance(ciphertext, list) or not all(isinstance(value, int) for value in ciphertext):
        origin = "oráculo encoder" if mode == "decoder_only" else "encoder"
        return {
            "outcome": "errored",
            "ciphertext": [],
            "decrypted_message": None,
            "decoder_output": "",
            "decoder_stderr": "",
            "error": enc_error or f"Saída do {origin} inválida.",
        }

    expected = [pow(ord(character), e, n) for character in message]
    decrypted, dec_error, decoder_output, decoder_stderr = run_program(
        decoder,
        {"ciphertext": ciphertext, "private_key": {"n": n, "d": d}},
    )
    decrypted_message = decrypted.get("message") if isinstance(decrypted, dict) else None

    if ciphertext != expected:
        decoder_label = "oráculo decoder" if mode == "encoder_only" else "decoder"
        if isinstance(decrypted_message, str):
            diagnostic = f"Texto devolvido pelo {decoder_label}: {preview_message(decrypted_message)!r}."
        else:
            diagnostic = f"O {decoder_label} nao devolveu texto legivel: {dec_error or 'saida invalida.'}"
        return {
            "outcome": "failed",
            "ciphertext": ciphertext,
            "decrypted_message": decrypted_message if isinstance(decrypted_message, str) else None,
            "decoder_output": decoder_output,
            "decoder_stderr": decoder_stderr,
            "error": (
                "O ciphertext nao corresponde ao RSA esperado. "
                f"Mensagem de teste: {preview_message(message)!r}. {diagnostic} "
                f"Saida bruta do decoder: {preview_output(decoder_output)}. "
                f"Stderr do decoder: {preview_output(decoder_stderr)}"
            ),
        }

    if not isinstance(decrypted_message, str):
        return {
            "outcome": "errored",
            "ciphertext": ciphertext,
            "decrypted_message": None,
            "decoder_output": decoder_output,
            "decoder_stderr": decoder_stderr,
            "error": dec_error or "O decoder nao devolveu uma mensagem valida.",
        }

    if decrypted_message != message:
        return {
            "outcome": "failed",
            "ciphertext": ciphertext,
            "decrypted_message": decrypted_message,
            "decoder_output": decoder_output,
            "decoder_stderr": decoder_stderr,
            "error": (
                "A mensagem nao foi descriptografada corretamente. "
                f"Esperado: {preview_message(message)!r}. Recebido: {preview_message(decrypted_message)!r}."
            ),
        }

    return {
        "outcome": "approved",
        "ciphertext": ciphertext,
        "decrypted_message": decrypted_message,
        "decoder_output": decoder_output,
        "decoder_stderr": decoder_stderr,
        "error": "",
    }


def run_custom_test(
    zip_path: Path,
    message: str,
    p: int,
    q: int,
    e: int,
    mode: str = "auto",
) -> dict:
    """Compile an upload (with optional oracle) and execute one student-selected RSA round trip."""
    n, d = validate_custom_test_input(message, p, q, e)
    resolved_mode = resolve_test_mode(zip_path, mode)

    with tempfile.TemporaryDirectory(prefix="rsa-custom-test-") as directory:
        encoder, decoder, compile_error = _prepare_student_and_oracle_programs(zip_path, resolved_mode, Path(directory))
        if compile_error or not encoder or not decoder:
            return {
                "mode": resolved_mode,
                "oracle_used": {"encoder": resolved_mode == "decoder_only", "decoder": resolved_mode == "encoder_only"},
                "status": "error",
                "ciphertext": [],
                "decrypted_message": None,
                "decoder_output": "",
                "decoder_stderr": "",
                "error": compile_error,
            }

        case_result = _evaluate_single_case(encoder, decoder, message, n, e, d, mode=resolved_mode)
        status_map = {"approved": "passed", "failed": "failed", "errored": "error"}
        return {
            "mode": resolved_mode,
            "oracle_used": {"encoder": resolved_mode == "decoder_only", "decoder": resolved_mode == "encoder_only"},
            "status": status_map[case_result["outcome"]],
            "ciphertext": case_result["ciphertext"],
            "decrypted_message": case_result["decrypted_message"],
            "decoder_output": case_result["decoder_output"],
            "decoder_stderr": case_result["decoder_stderr"],
            "error": case_result["error"],
        }


TEST_MESSAGES: tuple[str, ...] = (
    "Oi.",
    "Bom dia.",
    "RSA funciona.",
    "Envie seu ZIP.",
    "Uma chave publica cifra.",
    "A chave privada decifra a mensagem.",
    "Este caso verifica uma frase simples de ponta a ponta.",
    "O encoder deve transformar cada caractere em um valor RSA valido.",
    "O decoder deve recuperar exatamente o texto que foi enviado para a cifra.",
    "Pontuacao, espacos e numeros como 123 tambem precisam voltar sem mudancas.",
    "Uma equipe enviou o projeto antes do prazo e conferiu cada resultado com atencao.",
    "Durante a aula, os alunos aprenderam que a chave publica pode ser compartilhada, mas a chave privada deve permanecer protegida.",
    "O teste avalia uma mensagem comum e confirma que o algoritmo preserva letras maiusculas, minusculas, espacos, virgulas e pontos finais.",
    "Para passar nesta etapa, o programa precisa ler uma linha JSON, cifrar todos os caracteres recebidos e responder outra linha JSON sem mensagens extras no terminal.",
    "A implementacao correta nao depende de um texto especifico: ela deve funcionar com frases curtas, instrucoes completas, numeros e sinais de pontuacao usados pelo avaliador.",
    "Maria revisou o codigo com seu grupo. Primeiro eles verificaram o calculo de n, depois confirmaram o expoente publico e por fim testaram se a mensagem original aparecia apos a decifragem.",
    "Em uma demonstracao de laboratorio, cada participante enviou uma frase diferente. O sistema aceitou somente respostas bem formatadas e comparou a lista de numeros cifrados com o resultado matematico esperado.",
    "Quando uma mensagem possui muitos caracteres, um erro pequeno no loop pode fazer o ultimo simbolo desaparecer ou acrescentar lixo ao resultado. Por isso este caso compara o texto completo, do primeiro ao ultimo caractere.",
    "O grupo explicou que RSA usa operacoes de potenciacao modular. Mesmo sem revelar a chave privada, qualquer pessoa pode usar a chave publica para cifrar uma mensagem que somente o dono da chave privada conseguira ler sem erros.",
    "Antes de entregar, a equipe compactou encoder.c e decoder.c no mesmo arquivo ZIP. Os dois programas foram compilados separadamente, executados com entradas controladas e avaliados sem depender de arquivos temporarios criados pelo aluno.",
    "No fim da atividade, o professor pediu uma verificacao completa. A mensagem deveria entrar no encoder como texto comum, sair como uma lista de inteiros, entrar no decoder com a chave privada e retornar sem trocar, perder ou duplicar qualquer caractere.",
    "Este texto representa uma conversa de suporte: ola, preciso confirmar se meu algoritmo aceita espacos duplos, ponto final, virgula, ponto de interrogacao e os numeros 2026 e 150. A resposta esperada e simples: todos esses caracteres ASCII devem permanecer identicos depois do ciclo de cifra e decifra.",
    "Uma aplicacao de criptografia precisa lidar com entradas de tamanhos variados. Alguns usuarios digitam apenas uma saudacao; outros enviam instrucoes detalhadas para colegas de equipe. O avaliador usa ambos os cenarios para garantir que o codigo nao foi escrito apenas para uma frase conhecida e que o vetor de ciphertext tem o tamanho correto.",
    "Imagine uma entrega feita na ultima hora. O aluno abre o portal, seleciona o arquivo ZIP, aguarda a validacao e inicia a bateria. Em seguida, acompanha quantos casos foram aprovados para cada tamanho de primo. Se houver falha, ele deve conseguir revisar a leitura do JSON, a potencia modular e a montagem da mensagem decifrada antes de enviar uma nova versao.",
    "A seguranca do exercicio nao esta em decorar uma saida. O encoder recebe uma chave publica formada por n e e, calcula o valor cifrado de cada caractere e produz uma lista JSON. O decoder recebe a mesma lista, usa n e d e reconstrui o texto. Esta frase maior verifica que os dois executaveis mantem o contrato mesmo quando a entrada contem varias oracoes, numeros e sinais comuns.",
    "Nesta etapa final, os testes cobrem primos de dois ate nove digitos. A diferenca entre as chaves obriga a implementacao a tratar os valores recebidos como dados, e nao como constantes escondidas no codigo. Uma solucao robusta le a chave de cada entrada, calcula todas as operacoes com cuidado e devolve somente o JSON solicitado. Qualquer texto valido deve sobreviver ao percurso completo entre o encoder e o decoder.",
    "O relatorio do laboratorio registrou o seguinte: a equipe criou dois programas em C, definiu uma entrada JSON por linha e evitou imprimir mensagens de depuracao na saida padrao. Depois, testou frases pequenas e grandes, observou que cada caractere era cifrado separadamente e confirmou que a decifragem reproduzia o texto original. Esse comportamento precisa continuar correto quando mudamos o tamanho dos primos e, consequentemente, os valores de n, e e d.",
    "Uma boa estrategia de desenvolvimento e comecar com uma saudacao curta, inspecionar a lista de numeros retornada pelo encoder e entao aumentar gradualmente o tamanho da entrada. Se um caso longo falhar, vale conferir limites de buffers, terminadores de string, alocacao de memoria e leitura da linha completa. O avaliador nao exige uma biblioteca especifica de JSON, mas exige que a interface combinada seja respeitada: uma entrada JSON, uma saida JSON e nenhum texto adicional.",
    "Considere agora uma mensagem enviada entre dois integrantes de um grupo. Ana prepara um aviso com data, horario e instrucoes; Bruno recebe os numeros cifrados e usa a chave privada para recuperar o aviso. O teste espera que o resultado tenha as mesmas letras, espacos e pontuacoes do original. Ao repetir o processo com chaves construidas a partir de primos maiores, o software demonstra que sua logica RSA e geral e nao depende de valores pequenos escolhidos durante o desenvolvimento inicial.",
    "Este e o maior caso da bateria. Ele simula uma explicacao extensa para uma equipe que esta revisando sua entrega de criptografia. O texto lembra que cada caractere ASCII deve ser convertido para um numero, elevado ao expoente publico modulo n e colocado na ordem correta da lista de ciphertext. Na volta, cada numero deve ser elevado ao expoente privado modulo n para recuperar exatamente o caractere correspondente. A solucao tambem precisa preservar o inicio e o fim da mensagem, pois cortar um unico simbolo ja torna a decifragem incorreta. Ao concluir, o aluno deve verificar se encoder.c e decoder.c estao na raiz ou em qualquer pasta do ZIP, desde que cada nome apareca apenas uma vez. Com todos esses cuidados, a mesma implementacao podera processar os trinta textos, das frases curtas a este paragrafo longo, usando chaves derivadas de primos com dois, tres, quatro, cinco, seis, sete, oito e nove digitos.",
)
PRIME_DIGITS: tuple[int, ...] = tuple(range(2, 10))
TOTAL_TESTS = len(TEST_MESSAGES) * len(PRIME_DIGITS)


def message_for(index: int) -> str:
    """Return one of the 30 fixed, human-readable messages used by the battery."""
    try:
        return TEST_MESSAGES[index - 1]
    except IndexError as exc:
        raise ValueError("O indice da mensagem deve estar entre 1 e 30.") from exc


def preview_message(message: str, limit: int = 180) -> str:
    """Keep an execution diagnostic useful without returning a full long test case."""
    return message if len(message) <= limit else f"{message[:limit]}..."


def preview_output(output: str, limit: int = 1_000) -> str:
    """Represent raw output visibly, including newlines and control characters."""
    truncated = output if len(output) <= limit else f"{output[:limit]}..."
    return json.dumps(truncated, ensure_ascii=True)


def test_cases() -> list[TestCase]:
    cases: list[TestCase] = []
    for digits in PRIME_DIGITS:
        n, e, d = key_for_digits(digits)
        for index in range(1, 31):
            cases.append(TestCase(index, digits, message_for(index), n, e, d))
    return cases


def run_test_battery(zip_path: Path, mode: str = "auto") -> dict:
    """Compile student files (paired with menu oracle if partial) and run the 240-case battery."""
    resolved_mode = resolve_test_mode(zip_path, mode)
    buckets = {str(digit): {"total": 0, "approved": 0, "failed": 0, "errored": 0} for digit in PRIME_DIGITS}
    approved = failed = errored = 0
    last_error = ""

    with tempfile.TemporaryDirectory(prefix="rsa-test-") as directory:
        encoder, decoder, compile_error = _prepare_student_and_oracle_programs(zip_path, resolved_mode, Path(directory))
        if compile_error or not encoder or not decoder:
            for bucket in buckets.values():
                bucket["total"] = 30
                bucket["errored"] = 30
            return {
                "mode": resolved_mode,
                "total": TOTAL_TESTS,
                "approved": 0,
                "failed": 0,
                "errored": TOTAL_TESTS,
                "buckets": buckets,
                "last_error": compile_error,
            }

        for case in test_cases():
            bucket = buckets[str(case.prime_digits)]
            bucket["total"] += 1
            case_result = _evaluate_single_case(encoder, decoder, case.message, case.n, case.e, case.d, mode=resolved_mode)
            outcome = case_result["outcome"]
            if outcome != "approved":
                last_error = case_result["error"]

            bucket[outcome] += 1
            if outcome == "approved":
                approved += 1
            elif outcome == "failed":
                failed += 1
            else:
                errored += 1

    return {
        "mode": resolved_mode,
        "total": TOTAL_TESTS,
        "approved": approved,
        "failed": failed,
        "errored": errored,
        "buckets": buckets,
        "last_error": last_error,
    }


# This battery is intentionally independent from the student's 240-case battery.
# It is used by a teacher to verify that an encoder from one group interoperates
# with a decoder from another group.
GROUP_TEST_MESSAGES: tuple[str, ...] = (
    "Na reuniao de segunda-feira, a equipe revisou o cronograma da entrega, distribuiu as tarefas pendentes e combinou que toda alteracao no algoritmo seria registrada antes do proximo encontro.",
    "O professor explicou que uma interface bem definida permite que programas escritos por pessoas diferentes troquem dados sem depender de detalhes internos ou de uma mesma biblioteca.",
    "Depois de comparar os resultados, os estudantes perceberam que uma cifra correta precisa preservar cada caractere da mensagem, inclusive espacos, numeros, virgulas, pontos e sinais de interrogacao.",
    "Durante o laboratorio, Ana enviou um aviso para Bruno com horario, sala e material necessario; Bruno recebeu somente os numeros cifrados e recuperou o aviso sem alterar uma unica palavra.",
    "Uma equipe organizada testa primeiro exemplos pequenos, depois mensagens maiores e por fim casos inesperados, pois um erro em um limite de buffer costuma aparecer quando o texto cresce.",
    "O relatorio tecnico deve informar quais chaves foram usadas, quantos caracteres foram processados e se a mensagem final corresponde exatamente ao texto original que entrou no encoder.",
    "Quando dois grupos implementam partes complementares do mesmo protocolo, o resultado so pode ser considerado compativel se a saida produzida por um for aceita como entrada valida pelo outro.",
    "A aplicacao recebeu uma mensagem longa com instrucoes para a apresentacao final, incluindo a ordem dos slides, o tempo reservado para perguntas e a necessidade de manter todos os arquivos no repositorio.",
    "No fim da aula, os alunos conferiram que o encoder imprimiu apenas uma linha JSON e que o decoder respondeu no mesmo formato, sem mensagens de depuracao misturadas na saida.",
    "Um bom teste de interoperabilidade nao assume que os dois programas foram escritos da mesma forma; ele apenas verifica o contrato publico, a matematica RSA e a mensagem que deve voltar ao final.",
    "Carla revisou cada funcao de potencia modular porque valores grandes podem revelar multiplicacoes inseguras, conversoes inadequadas de tipo ou lacos que demoram mais do que o limite permitido.",
    "A equipe decidiu documentar o formato do ciphertext como uma lista ordenada de inteiros, pois a ordem representa a sequencia dos caracteres e qualquer troca de posicao muda toda a mensagem recuperada.",
    "Em uma simulacao de suporte, o usuario informou que precisava enviar dados para outro setor; o sistema cifrou a frase com a chave publica e somente o destinatario autorizado conseguiu le-la novamente.",
    "Os integrantes do grupo combinaram que cada nova versao seria enviada ao portal depois de uma revisao curta, permitindo que o professor sempre encontrasse o arquivo mais recente para avaliar a integracao.",
    "A atividade mostra que criptografia aplicada nao depende apenas de formulas: tambem exige leitura correta de entrada, validacao de dados, tratamento de erros e respeito rigoroso ao formato combinado.",
    "Ao testar com chaves de tamanhos diferentes, os alunos confirmaram que o programa nao guardava valores fixos no codigo e que calculava cada operacao a partir de n, e e d recebidos na entrada.",
    "O coordenador pediu uma mensagem detalhada para a equipe: tragam os computadores carregados, confirmem a presenca ate as nove horas e deixem os arquivos de demonstracao prontos antes da banca chegar.",
    "Mesmo uma frase aparentemente simples pode falhar se o decoder esquecer o terminador de string, ignorar o ultimo numero da lista ou interpretar um valor cifrado com um tipo pequeno demais.",
    "A avaliacao entre grupos aproxima o trabalho de um sistema real, no qual componentes independentes precisam conversar corretamente sem que seus autores possam combinar atalhos fora da especificacao.",
    "Antes de publicar a versao final, o grupo executou casos com textos curtos, avisos de varias linhas e paragrafos extensos para observar se a memoria alocada e o processamento permaneciam estaveis.",
    "O teste seguinte representa um comunicado academico completo: a prova pratica ocorrera na proxima semana, o envio sera feito pelo portal e cada dupla devera demonstrar a cifra e a decifragem em tempo real.",
    "Para garantir resultados reproduziveis, o avaliador usa mensagens conhecidas e chaves construidas previamente, compara a lista de numeros obtida com o valor matematico esperado e registra qualquer divergencia.",
    "Na discussao do projeto, ficou claro que uma implementacao pode parecer correta quando cifra e decifra seus proprios dados, mas ainda falhar ao receber a saida de outro programa que segue o contrato oficial.",
    "O professor solicitou que a equipe verificasse cuidadosamente os limites dos inteiros, porque o produto de dois primos maiores e as multiplicacoes intermediarias podem ultrapassar tipos escolhidos sem planejamento.",
    "Uma mensagem de boas-vindas foi preparada para novos alunos: leiam o enunciado com atencao, testem suas funcoes gradualmente e enviem somente arquivos ZIP que contenham encoder.c e decoder.c.",
    "O grupo de desenvolvimento registrou que qualquer caractere ASCII imprimivel deve atravessar o ciclo completo sem ser removido, trocado ou duplicado, desde a primeira letra ate o ponto final.",
    "No experimento de hoje, uma equipe ficou responsavel pela cifra e outra pela decifragem; a comunicacao so foi aprovada quando o texto retornou identico, mesmo usando programas compilados separadamente.",
    "A secretaria enviou um lembrete detalhado sobre a apresentacao: confiram a lista de participantes, preparem exemplos visiveis e reservem alguns minutos para explicar as decisoes tecnicas adotadas.",
    "Quando a bateria encontra uma falha, o resultado ajuda a localizar se o problema esta na compilacao, no formato do JSON, no calculo do ciphertext ou na reconstrucao da mensagem pelo decoder.",
    "A colaboracao entre grupos exige responsabilidade sobre a ultima versao enviada, pois o portal seleciona o arquivo mais recente dos integrantes para representar aquele grupo durante a avaliacao do professor.",
    "Uma aplicacao confiavel nao deve depender de uma frase conhecida no codigo; ela precisa percorrer todos os caracteres recebidos, aplicar a operacao modular adequada e manter a sequencia original intacta.",
    "Os estudantes revisaram as mensagens de erro para que fossem uteis: em vez de apenas informar que algo falhou, o sistema indica se faltou uma chave, se a saida nao era JSON ou se o texto nao foi recuperado.",
    "O roteiro da demonstracao orienta cada dupla a explicar como escolheu os tipos numericos, como calculou a potencia modular e como verificou a compatibilidade da propria solucao com a de outro grupo.",
    "No encerramento do laboratorio, todos compararam os arquivos enviados e confirmaram que o encoder de uma equipe podia gerar dados que o decoder de outra equipe entendia sem qualquer ajuste manual.",
    "A mensagem inclui detalhes comuns de uma rotina universitaria: aula as oito horas, reuniao no laboratorio tres, entrega pelo ambiente virtual e revisao final feita com calma antes do prazo terminar.",
    "Se uma implementacao imprime textos auxiliares na saida padrao, o programa que recebe os dados pode nao conseguir interpretar o JSON; por isso os avisos de depuracao devem ficar fora do protocolo.",
    "Uma lista de ciphertext correta possui um numero para cada caractere da mensagem, na mesma ordem em que foi lido; comparar apenas o tamanho da lista nao basta para garantir que a cifra esta certa.",
    "O avaliador tambem observa a capacidade de lidar com frases extensas, porque textos maiores exercitam repeticoes, alocacao de memoria, leitura da entrada e montagem da resposta de forma mais completa.",
    "Durante a revisao cruzada, cada grupo aprendeu que padroes claros facilitam a manutencao: quem produz a informacao sabe o que enviar e quem consome sabe exatamente como validar cada campo recebido.",
    "A coordenacao enviou uma nota para a turma informando que a atividade valoriza clareza, corretude e testes; uma solucao curta pode ser excelente desde que respeite todos os requisitos do formato RSA combinado.",
    "A equipe executou a mesma mensagem com primos de varios tamanhos para confirmar que a logica permanecia geral, sem depender de exemplos pequenos usados apenas durante o desenvolvimento inicial.",
    "Em sistemas reais, componentes podem ser atualizados em momentos diferentes; por isso esta avaliacao usa a ultima versao enviada por cada grupo e verifica se elas ainda conseguem trabalhar juntas.",
    "O texto de teste descreve um pedido de ajuda completo: preciso verificar se meu programa aceita mensagens longas, se conserva a pontuacao e se retorna exatamente o conteudo que foi cifrado pela outra equipe.",
    "Uma falha de interoperabilidade pode indicar que os grupos interpretaram a chave de maneiras diferentes, adotaram campos incompativeis ou deixaram de seguir a ordem esperada para os numeros da mensagem cifrada.",
    "Para encerrar a atividade, o professor pediu que todos refletissem sobre a diferenca entre testar uma funcao isolada e validar uma comunicacao inteira entre programas independentes e autores diferentes.",
    "O sistema registra o total de casos aprovados, falhos e com erro de execucao, oferecendo ao professor um panorama objetivo antes de analisar os detalhes de qualquer divergencia encontrada.",
    "A ultima mensagem lembra que uma boa entrega combina matematica correta, codigo cuidadoso e comunicacao precisa entre encoder e decoder, permitindo que qualquer grupo compativel participe do mesmo experimento.",
    "Ao receber o resultado da bateria, os grupos devem revisar com serenidade cada detalhe apontado, corrigir o contrato quando necessario e enviar uma nova versao para que a proxima avaliacao use o arquivo atualizado.",
    "Esta verificacao final usa textos compreensiveis para pessoas, mas o criterio permanece tecnico: a cifra precisa obedecer ao RSA, o decoder precisa entender o ciphertext produzido e a mensagem deve voltar sem alteracoes.",
    "Com cinquenta cenarios longos e variados, a avaliacao procura mostrar que interoperabilidade nao e sorte: ela nasce de uma especificacao simples, implementada com disciplina e testada por programas diferentes.",
)
GROUP_TOTAL_TESTS = len(GROUP_TEST_MESSAGES) * len(PRIME_DIGITS)


def group_test_cases() -> list[TestCase]:
    cases: list[TestCase] = []
    for digits in PRIME_DIGITS:
        n, e, d = key_for_digits(digits)
        for index, message in enumerate(GROUP_TEST_MESSAGES, start=1):
            cases.append(TestCase(index, digits, message, n, e, d))
    return cases


def _compile_cross_programs(
    encoder_zip_path: Path,
    decoder_zip_path: Path,
    root: Path,
) -> tuple[Path | None, Path | None, str, str]:
    """Validate and compile each group's assigned role independently."""
    encoder: Path | None = None
    decoder: Path | None = None
    encoder_error = ""
    decoder_error = ""

    try:
        validate_test_sources(encoder_zip_path, "encoder_only")
        encoder_source = extract_source(encoder_zip_path, "encoder.c", root / "enc")
        encoder_bin = root / "encoder"
        encoder_ok, encoder_log = compile_source(encoder_source, encoder_bin)
        if encoder_ok:
            encoder = encoder_bin
        else:
            encoder_error = f"Falha de compilação do encoder: {encoder_log}"
    except TestExecutionError as exc:
        encoder_error = str(exc)

    try:
        validate_test_sources(decoder_zip_path, "decoder_only")
        decoder_source = extract_source(decoder_zip_path, "decoder.c", root / "dec")
        decoder_bin = root / "decoder"
        decoder_ok, decoder_log = compile_source(decoder_source, decoder_bin)
        if decoder_ok:
            decoder = decoder_bin
        else:
            decoder_error = f"Falha de compilação do decoder: {decoder_log}"
    except TestExecutionError as exc:
        decoder_error = str(exc)

    return encoder, decoder, encoder_error, decoder_error


def _evaluate_cross_case(
    encoder: Path | None,
    decoder: Path | None,
    message: str,
    n: int,
    e: int,
    d: int,
    prime_digits: int,
    encoder_compile_error: str = "",
    decoder_compile_error: str = "",
) -> dict:
    """Evaluate both groups independently and together so blame is always unambiguous."""
    expected = [pow(ord(character), e, n) for character in message]

    ciphertext: list[int] = []
    if encoder is None:
        encoder_status = "error"
        encoder_error = encoder_compile_error
    else:
        encrypted, enc_raw_err, _, _ = run_program(encoder, {"message": message, "public_key": {"n": n, "e": e}})
        raw_ct = encrypted.get("ciphertext") if isinstance(encrypted, dict) else None
        if not isinstance(raw_ct, list) or not all(isinstance(value, int) for value in raw_ct):
            encoder_status = "error"
            encoder_error = enc_raw_err or f"Saida do encoder invalida ({prime_digits} digitos)."
        elif raw_ct != expected:
            encoder_status = "failed"
            ciphertext = raw_ct
            encoder_error = (
                f"O ciphertext do grupo encoder nao corresponde ao RSA esperado ({prime_digits} digitos). "
                f"Mensagem: {preview_message(message)!r}."
            )
        else:
            encoder_status = "passed"
            ciphertext = raw_ct
            encoder_error = ""

    # When encoder fails, pass the expected RSA ciphertext to the decoder so we isolate whether the decoder group works.
    decoder_input = ciphertext if encoder_status == "passed" else expected
    if decoder is None:
        decoder_status = "error"
        decoder_error = decoder_compile_error
        decrypted_message = None
        decoder_output = ""
        decoder_stderr = ""
    else:
        decrypted, dec_raw_err, decoder_output, decoder_stderr = run_program(
            decoder,
            {"ciphertext": decoder_input, "private_key": {"n": n, "d": d}},
        )
        decrypted_message = decrypted.get("message") if isinstance(decrypted, dict) else None
        if not isinstance(decrypted_message, str):
            decoder_status = "error"
            decoder_error = (
                dec_raw_err
                or f"Saida do decoder invalida ({prime_digits} digitos). Stdout: {preview_output(decoder_output)}. Stderr: {preview_output(decoder_stderr)}"
            )
            decrypted_message = None
        elif decrypted_message != message:
            decoder_status = "failed"
            decoder_error = (
                f"O decoder nao recuperou a mensagem original ({prime_digits} digitos). "
                f"Esperado: {preview_message(message)!r}. Recebido: {preview_message(decrypted_message)!r}."
            )
        else:
            decoder_status = "passed"
            decoder_error = ""

    if encoder_status == "passed" and decoder_status == "passed":
        overall = "approved"
    elif encoder_status == "error" or decoder_status == "error":
        overall = "errored"
    else:
        overall = "failed"

    return {
        "overall": overall,
        "encoder_status": encoder_status,
        "decoder_status": decoder_status,
        "encoder_error": encoder_error,
        "decoder_error": decoder_error,
        "ciphertext": ciphertext,
        "decrypted_message": decrypted_message,
        "decoder_output": decoder_output,
        "decoder_stderr": decoder_stderr,
    }


def _determine_faulty_side(encoder_has_issue: bool, decoder_has_issue: bool) -> str:
    if encoder_has_issue and decoder_has_issue:
        return "both"
    if encoder_has_issue:
        return "encoder"
    if decoder_has_issue:
        return "decoder"
    return "none"


def _compose_cross_error(faulty_side: str, encoder_error: str, decoder_error: str) -> str:
    if faulty_side == "both":
        return f"Falha em ambos os grupos. Encoder: {encoder_error} | Decoder: {decoder_error}"
    if faulty_side == "encoder":
        return f"Falha no grupo Encoder: {encoder_error}"
    if faulty_side == "decoder":
        return f"Falha no grupo Decoder: {decoder_error}"
    return ""


def run_group_test_battery(encoder_zip_path: Path, decoder_zip_path: Path) -> dict:
    """Run 400 interoperability cases and identify clearly which group (encoder/decoder/both) failed."""
    approved = failed = errored = 0
    enc_approved = enc_failed = enc_errored = 0
    dec_approved = dec_failed = dec_errored = 0
    enc_last_error = ""
    dec_last_error = ""

    with tempfile.TemporaryDirectory(prefix="rsa-group-test-") as directory:
        encoder, decoder, enc_compile_err, dec_compile_err = _compile_cross_programs(
            encoder_zip_path, decoder_zip_path, Path(directory)
        )
        if encoder is None and decoder is None:
            return {
                "total": GROUP_TOTAL_TESTS,
                "approved": 0,
                "failed": 0,
                "errored": GROUP_TOTAL_TESTS,
                "faulty_side": "both",
                "encoder_stats": {"approved": 0, "failed": 0, "errored": GROUP_TOTAL_TESTS, "last_error": enc_compile_err},
                "decoder_stats": {"approved": 0, "failed": 0, "errored": GROUP_TOTAL_TESTS, "last_error": dec_compile_err},
                "last_error": _compose_cross_error("both", enc_compile_err, dec_compile_err),
            }

        for case in group_test_cases():
            res = _evaluate_cross_case(
                encoder,
                decoder,
                case.message,
                case.n,
                case.e,
                case.d,
                case.prime_digits,
                enc_compile_err,
                dec_compile_err,
            )

            if res["encoder_status"] == "passed":
                enc_approved += 1
            elif res["encoder_status"] == "failed":
                enc_failed += 1
                enc_last_error = res["encoder_error"]
            else:
                enc_errored += 1
                enc_last_error = res["encoder_error"]

            if res["decoder_status"] == "passed":
                dec_approved += 1
            elif res["decoder_status"] == "failed":
                dec_failed += 1
                dec_last_error = res["decoder_error"]
            else:
                dec_errored += 1
                dec_last_error = res["decoder_error"]

            if res["overall"] == "approved":
                approved += 1
            elif res["overall"] == "failed":
                failed += 1
            else:
                errored += 1

    faulty_side = _determine_faulty_side(
        (enc_failed + enc_errored) > 0,
        (dec_failed + dec_errored) > 0,
    )
    last_error = _compose_cross_error(faulty_side, enc_last_error, dec_last_error)

    return {
        "total": GROUP_TOTAL_TESTS,
        "approved": approved,
        "failed": failed,
        "errored": errored,
        "faulty_side": faulty_side,
        "encoder_stats": {
            "approved": enc_approved,
            "failed": enc_failed,
            "errored": enc_errored,
            "last_error": enc_last_error,
        },
        "decoder_stats": {
            "approved": dec_approved,
            "failed": dec_failed,
            "errored": dec_errored,
            "last_error": dec_last_error,
        },
        "last_error": last_error,
    }


def run_group_custom_test(encoder_zip_path: Path, decoder_zip_path: Path, message: str, p: int, q: int, e: int) -> dict:
    """Run one teacher-configured interoperability test between two group uploads."""
    n, d = validate_custom_test_input(message, p, q, e)
    with tempfile.TemporaryDirectory(prefix="rsa-group-custom-test-") as directory:
        encoder, decoder, enc_compile_err, dec_compile_err = _compile_cross_programs(
            encoder_zip_path, decoder_zip_path, Path(directory)
        )
        res = _evaluate_cross_case(
            encoder,
            decoder,
            message,
            n,
            e,
            d,
            len(str(p)),
            enc_compile_err,
            dec_compile_err,
        )

    faulty_side = _determine_faulty_side(
        res["encoder_status"] != "passed",
        res["decoder_status"] != "passed",
    )
    status_map = {"approved": "passed", "failed": "failed", "errored": "error"}
    combined_error = _compose_cross_error(faulty_side, res["encoder_error"], res["decoder_error"])

    return {
        "status": status_map[res["overall"]],
        "faulty_side": faulty_side,
        "encoder_status": res["encoder_status"],
        "decoder_status": res["decoder_status"],
        "encoder_error": res["encoder_error"],
        "decoder_error": res["decoder_error"],
        "ciphertext": res["ciphertext"],
        "decrypted_message": res["decrypted_message"],
        "decoder_output": res["decoder_output"],
        "decoder_stderr": res["decoder_stderr"],
        "error": combined_error,
    }
