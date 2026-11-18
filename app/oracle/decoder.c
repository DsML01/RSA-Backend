/*
 * Modelo alternativo de decoder, inspirado em descriptografar() do menu.c legado.
 *
 * Entrada: {"ciphertext":[...],"private_key":{"n":323,"d":2753}}
 * Saida:   {"message":"..."}
 */

#include <ctype.h>
#include <inttypes.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define INPUT_CAPACITY 32768
#define MAX_MESSAGE_LENGTH 1000

static uint64_t mod_mul(uint64_t a, uint64_t b, uint64_t modulo) {
    return (uint64_t)((__uint128_t)a * b % modulo);
}

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

/* Funcao que o grupo decodificador deve implementar: uma decifra RSA por valor. */
static int descriptografar(
    const uint64_t mensagem_encriptada[],
    size_t tamanho,
    unsigned char mensagem[],
    uint64_t d,
    uint64_t n
) {
    for (size_t indice = 0; indice < tamanho; ++indice) {
        uint64_t valor = mod_pow(mensagem_encriptada[indice], d, n);
        if (valor < 32 || valor > 126) {
            return 1;
        }
        mensagem[indice] = (unsigned char)valor;
    }
    mensagem[tamanho] = '\0';
    return 0;
}

int main(void) {
    char entrada[INPUT_CAPACITY];
    char *inicio_lista;
    char *fim_lista;
    char *texto_n;
    char *texto_d;
    char *cursor;
    uint64_t ciphertext[MAX_MESSAGE_LENGTH];
    unsigned char mensagem[MAX_MESSAGE_LENGTH + 1];
    size_t tamanho = 0;
    uint64_t n;
    uint64_t d;

    if (!fgets(entrada, sizeof(entrada), stdin)) {
        return 1;
    }

    inicio_lista = strchr(entrada, '[');
    fim_lista = strchr(entrada, ']');
    texto_n = strstr(entrada, "\"n\":");
    texto_d = strstr(entrada, "\"d\":");
    if (!inicio_lista || !fim_lista || !texto_n || !texto_d || fim_lista < inicio_lista) {
        return 2;
    }
    if (sscanf(texto_n, "\"n\":%" SCNu64, &n) != 1 ||
        sscanf(texto_d, "\"d\":%" SCNu64, &d) != 1 || n == 0) {
        return 3;
    }

    cursor = inicio_lista + 1;
    while (cursor < fim_lista) {
        char *proximo;

        while (cursor < fim_lista && (isspace((unsigned char)*cursor) || *cursor == ',')) {
            ++cursor;
        }
        if (cursor == fim_lista) {
            break;
        }
        if (tamanho == MAX_MESSAGE_LENGTH) {
            return 4;
        }

        ciphertext[tamanho] = strtoull(cursor, &proximo, 10);
        if (proximo == cursor || proximo > fim_lista ||
            (proximo < fim_lista && *proximo != ',' && !isspace((unsigned char)*proximo))) {
            return 5;
        }
        ++tamanho;
        cursor = proximo;
    }

    if (descriptografar(ciphertext, tamanho, mensagem, d, n) != 0) {
        return 6;
    }
    printf("{\"message\":\"%s\"}\n", mensagem);
    return 0;
}
