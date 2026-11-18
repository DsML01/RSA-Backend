/*
 * Modelo alternativo de encoder, inspirado em encriptar() do menu.c legado.
 *
 * Entrada: {"message":"...","public_key":{"n":323,"e":17}}
 * Saida:   {"ciphertext":[...]} 
 */

#include <inttypes.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define INPUT_CAPACITY 4096
#define MAX_MESSAGE_LENGTH 1000

/* Evita overflow em (a * b) % modulo para n de ate aproximadamente 10^18. */
static uint64_t mod_mul(uint64_t a, uint64_t b, uint64_t modulo) {
    return (uint64_t)((__uint128_t)a * b % modulo);
}

/* Exponenciacao binaria: O(log expoente), em vez de multiplicar base repetidamente. */
static uint64_t mod_pow(uint64_t base, uint64_t expoente, uint64_t modulo) {
    uint64_t resultado = 1;
    base %= modulo;

    while (expoente > 0) {
        if ((expoente & 1U) != 0U) {
            resultado = mod_mul(resultado, base, modulo);
        }
        base = mod_mul(base, base, modulo);
        expoente >>= 1U;
    }
    return resultado;
}

/* Funcao que o grupo codificador deve implementar: uma cifra RSA por byte ASCII. */
static void encriptar(
    const unsigned char mensagem[],
    size_t tamanho,
    uint64_t mensagem_encriptada[],
    uint64_t n,
    uint64_t e
) {
    for (size_t indice = 0; indice < tamanho; ++indice) {
        mensagem_encriptada[indice] = mod_pow(mensagem[indice], e, n);
    }
}

int main(void) {
    char entrada[INPUT_CAPACITY];
    char *inicio_mensagem;
    char *fim_mensagem;
    char *texto_n;
    char *texto_e;
    size_t tamanho;
    uint64_t n;
    uint64_t e;
    uint64_t ciphertext[MAX_MESSAGE_LENGTH];

    if (!fgets(entrada, sizeof(entrada), stdin)) {
        return 1;
    }

    inicio_mensagem = strstr(entrada, "\"message\":\"");
    fim_mensagem = strstr(entrada, "\",\"public_key\"");
    texto_n = strstr(entrada, "\"n\":");
    texto_e = strstr(entrada, "\"e\":");
    if (!inicio_mensagem || !fim_mensagem || !texto_n || !texto_e) {
        return 2;
    }

    inicio_mensagem += strlen("\"message\":\"");
    tamanho = (size_t)(fim_mensagem - inicio_mensagem);
    if (fim_mensagem < inicio_mensagem || tamanho > MAX_MESSAGE_LENGTH) {
        return 3;
    }
    if (sscanf(texto_n, "\"n\":%" SCNu64, &n) != 1 ||
        sscanf(texto_e, "\"e\":%" SCNu64, &e) != 1 || n == 0) {
        return 4;
    }

    encriptar((const unsigned char *)inicio_mensagem, tamanho, ciphertext, n, e);

    printf("{\"ciphertext\":[");
    for (size_t indice = 0; indice < tamanho; ++indice) {
        printf("%s%" PRIu64, indice == 0 ? "" : ",", ciphertext[indice]);
    }
    puts("]}");
    return 0;
}
