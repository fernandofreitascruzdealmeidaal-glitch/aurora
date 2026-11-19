// Copie este arquivo para "config.h" (na mesma pasta) e preencha os valores.
// O config.h fica fora do Git (.gitignore) porque contém a senha do Wi-Fi e a chave.
#pragma once

// Rede Wi-Fi da escola (2,4 GHz — o ESP32 não conecta em redes só 5 GHz)
#define WIFI_SSID   "nome-da-rede"
#define WIFI_SENHA  "senha-da-rede"

// Endereço do servidor Aurora na rede local (veja no computador que roda o servidor)
#define AURORA_URL  "http://192.168.0.10:8000/api/v1/leituras"

// Gerados por: aurora novo-dispositivo sala-101 "Sala 101"
#define AURORA_DISPOSITIVO  "sala-101"
#define AURORA_CHAVE        "cole-a-chave-gerada-aqui"

// De quanto em quanto tempo medir (segundos). O SCD4x mede a cada 5 s; 60 é um bom valor.
#define INTERVALO_LEITURA_S 60

// 1 = sem sensor ligado: gera valores de teste (bom para testar Wi-Fi e servidor primeiro)
#define AURORA_SIMULAR 0

// Pinos I2C do sensor (padrão do ESP32 DevKit: SDA=21, SCL=22)
#define PINO_SDA 21
#define PINO_SCL 22
