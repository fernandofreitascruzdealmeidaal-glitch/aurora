// Aurora — firmware do sensor (ESP32 + Sensirion SCD40/SCD41)
//
// O que este programa garante:
//  - Nunca trava: watchdog reinicia o ESP32 se o loop parar por mais de 60 s.
//  - Não perde dados quando o Wi-Fi cai: guarda até 12 h de leituras na memória
//    e envia tudo quando a rede volta (as mais antigas saem se o buffer encher).
//  - Não duplica dados: cada leitura tem um ID único; reenviar é seguro.
//  - Não envia lixo: leituras fora da faixa do sensor são descartadas aqui mesmo.
//  - Não precisa de relógio: envia "idade_s" (há quantos segundos mediu) e o servidor
//    calcula a hora certa.
//  - Recupera o sensor sozinho: se o SCD4x parar de responder, é reiniciado.

#include <Arduino.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <Wire.h>
#include <esp_arduino_version.h>
#include <esp_task_wdt.h>

// Nome próprio de propósito: o ESP32 já tem arquivos chamados "config.h" que seriam lidos no lugar.
#if __has_include("aurora_config.h")
#include "aurora_config.h"
#elif defined(AURORA_USAR_EXEMPLO)
#include "aurora_config.example.h"
#else
#error "Copie include/aurora_config.example.h para include/aurora_config.h e preencha Wi-Fi, URL e chave."
#endif

#ifdef AURORA_FORCAR_SIMULACAO
#undef AURORA_SIMULAR
#define AURORA_SIMULAR 1
#endif

#if !AURORA_SIMULAR
#include <SensirionI2cScd4x.h>
#endif

// ------------------------------------------------------------------ parâmetros

constexpr uint32_t WATCHDOG_S = 60;
constexpr size_t CAPACIDADE_BUFFER = 720;  // 12 h com 1 leitura/min (~12 KB de RAM)
constexpr size_t MAX_POR_ENVIO = 50;       // leituras por requisição HTTP
constexpr uint32_t TEMPO_HTTP_MS = 10000;
constexpr uint32_t ESPERA_WIFI_MS = 30000;
constexpr uint32_t ESPERA_MIN_MS = 5000;     // atraso inicial após falha de envio
constexpr uint32_t ESPERA_MAX_MS = 300000;   // no máximo 5 min entre tentativas
constexpr uint32_t ESPERA_CHAVE_MS = 600000; // chave errada: tenta de novo a cada 10 min
constexpr uint8_t FALHAS_PARA_REINICIAR_SENSOR = 5;
constexpr uint8_t FALHAS_PARA_REINICIAR_ESP = 30;

// Mesmas faixas físicas do servidor (aurora/limites.py)
constexpr float TEMP_MIN = -10.0f, TEMP_MAX = 60.0f;
constexpr float UMID_MIN = 0.0f, UMID_MAX = 100.0f;
constexpr uint16_t CO2_MIN = 250, CO2_MAX = 10000;

// ------------------------------------------------------------------ buffer circular

struct Leitura {
  uint32_t contador;
  uint32_t medidoMs;  // millis() no momento da medição
  float temperatura;
  float umidade;
  uint16_t co2;
};

Leitura buffer[CAPACIDADE_BUFFER];
size_t inicioBuffer = 0;
size_t quantidade = 0;
uint32_t descartadasPorBufferCheio = 0;

void guardar(const Leitura& l) {
  if (quantidade == CAPACIDADE_BUFFER) {  // cheio: abre espaço tirando a mais antiga
    inicioBuffer = (inicioBuffer + 1) % CAPACIDADE_BUFFER;
    quantidade--;
    descartadasPorBufferCheio++;
  }
  buffer[(inicioBuffer + quantidade) % CAPACIDADE_BUFFER] = l;
  quantidade++;
}

const Leitura& noBuffer(size_t i) { return buffer[(inicioBuffer + i) % CAPACIDADE_BUFFER]; }

void retirar(size_t n) {
  if (n > quantidade) n = quantidade;
  inicioBuffer = (inicioBuffer + n) % CAPACIDADE_BUFFER;
  quantidade -= n;
}

// ------------------------------------------------------------------ estado

char idBoot[9];  // 8 dígitos hex aleatórios a cada ligação → IDs únicos mesmo após reiniciar
uint32_t contadorLeituras = 0;
uint32_t ultimaLeituraMs = 0;
uint32_t ultimaTentativaWifiMs = 0;
uint32_t proximoEnvioMs = 0;
uint32_t esperaAtualMs = ESPERA_MIN_MS;
uint8_t falhasSensor = 0;
bool primeiraLeitura = true;

#if !AURORA_SIMULAR
SensirionI2cScd4x sensor;
#endif

// Comparação de tempo que continua certa quando millis() dá a volta (a cada ~49 dias).
// Só funciona para momentos a menos de ~24 dias de distância — por isso as marcas de tempo
// guardadas abaixo são renovadas com frequência, nunca deixadas "envelhecer".
bool jaPassou(uint32_t momentoMs) { return (int32_t)(millis() - momentoMs) >= 0; }

// ------------------------------------------------------------------ watchdog

void iniciarWatchdog() {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  esp_task_wdt_config_t cfg = {
      .timeout_ms = WATCHDOG_S * 1000,
      .idle_core_mask = 0,
      .trigger_panic = true,
  };
  if (esp_task_wdt_reconfigure(&cfg) != ESP_OK) esp_task_wdt_init(&cfg);
#else
  esp_task_wdt_init(WATCHDOG_S, true);
#endif
  esp_task_wdt_add(nullptr);
}

// ------------------------------------------------------------------ sensor

bool iniciarSensor() {
#if AURORA_SIMULAR
  Serial.println("[sensor] modo SIMULADO (sem sensor real)");
  return true;
#else
  Wire.begin(PINO_SDA, PINO_SCL);
  sensor.begin(Wire, SCD41_I2C_ADDR_62);
  delay(30);
  sensor.wakeUp();
  sensor.stopPeriodicMeasurement();
  sensor.reinit();
  int16_t erro = sensor.startPeriodicMeasurement();
  if (erro != 0) {
    Serial.printf("[sensor] falha ao iniciar SCD4x (erro %d). Confira os fios SDA/SCL/3V3/GND.\n", erro);
    return false;
  }
  Serial.println("[sensor] SCD4x iniciado; primeira medição em ~5 s");
  return true;
#endif
}

bool valida(const Leitura& l) {
  if (isnan(l.temperatura) || isnan(l.umidade)) return false;
  if (l.temperatura < TEMP_MIN || l.temperatura > TEMP_MAX) return false;
  if (l.umidade < UMID_MIN || l.umidade > UMID_MAX) return false;
  if (l.co2 < CO2_MIN || l.co2 > CO2_MAX) return false;
  return true;
}

bool medir(Leitura& l) {
#if AURORA_SIMULAR
  float t = millis() / 60000.0f;
  l.co2 = (uint16_t)(700 + 300 * sinf(t / 10.0f) + random(-20, 20));
  l.temperatura = 24.0f + 1.5f * sinf(t / 30.0f);
  l.umidade = 55.0f + 5.0f * cosf(t / 25.0f);
  return true;
#else
  bool pronta = false;
  if (sensor.getDataReadyStatus(pronta) != 0 || !pronta) return false;
  uint16_t co2 = 0;
  float temperatura = NAN, umidade = NAN;
  if (sensor.readMeasurement(co2, temperatura, umidade) != 0) return false;
  l.co2 = co2;  // o SCD4x devolve 0 quando a medição não é válida → recusado em valida()
  l.temperatura = temperatura;
  l.umidade = umidade;
  return true;
#endif
}

void tentarMedir() {
  Leitura l{};
  l.contador = ++contadorLeituras;
  l.medidoMs = millis();
  if (medir(l) && valida(l)) {
    falhasSensor = 0;
    guardar(l);
    Serial.printf("[leitura] #%lu CO2=%u ppm  T=%.1f C  UR=%.0f%%  (buffer: %u)\n",
                  (unsigned long)l.contador, l.co2, l.temperatura, l.umidade, (unsigned)quantidade);
    return;
  }
  falhasSensor++;
  Serial.printf("[sensor] leitura falhou ou fora da faixa (%u seguida(s))\n", falhasSensor);
  if (falhasSensor == FALHAS_PARA_REINICIAR_SENSOR) {
    Serial.println("[sensor] reiniciando o sensor...");
    iniciarSensor();
  } else if (falhasSensor >= FALHAS_PARA_REINICIAR_ESP) {
    Serial.println("[sensor] sensor não se recuperou; reiniciando o ESP32");
    delay(100);
    ESP.restart();
  }
}

// ------------------------------------------------------------------ Wi-Fi

void cuidarDoWifi() {
  if (WiFi.status() == WL_CONNECTED) {
    ultimaTentativaWifiMs = 0;  // se cair, tenta reconectar na hora (e não guarda um tempo velho)
    return;
  }
  if (ultimaTentativaWifiMs != 0 && !jaPassou(ultimaTentativaWifiMs + ESPERA_WIFI_MS)) return;
  ultimaTentativaWifiMs = millis();
  if (ultimaTentativaWifiMs == 0) ultimaTentativaWifiMs = 1;
  Serial.printf("[wifi] conectando em \"%s\"...\n", WIFI_SSID);
  WiFi.disconnect();
  WiFi.begin(WIFI_SSID, WIFI_SENHA);
}

// ------------------------------------------------------------------ envio

void agendarNovaTentativa(uint32_t esperaMs) {
  proximoEnvioMs = millis() + esperaMs;
  Serial.printf("[envio] nova tentativa em %lu s\n", (unsigned long)(esperaMs / 1000));
}

void enviarLote() {
  if (!jaPassou(proximoEnvioMs)) return;
  // Mantém a marca de tempo sempre recente: um valor com mais de ~24 dias pareceria "no futuro"
  // para jaPassou() e travaria os envios (ex.: férias inteiras sem Wi-Fi).
  proximoEnvioMs = millis();
  if (quantidade == 0 || WiFi.status() != WL_CONNECTED) return;

  const size_t n = quantidade < MAX_POR_ENVIO ? quantidade : MAX_POR_ENVIO;
  const uint32_t agora = millis();

  JsonDocument doc;
  JsonArray lista = doc["leituras"].to<JsonArray>();
  char id[24];
  for (size_t i = 0; i < n; i++) {
    const Leitura& l = noBuffer(i);
    snprintf(id, sizeof(id), "%s-%lu", idBoot, (unsigned long)l.contador);
    JsonObject o = lista.add<JsonObject>();
    o["id_leitura"] = id;
    o["idade_s"] = (agora - l.medidoMs) / 1000;  // subtração sem sinal: certa mesmo com overflow
    o["temperatura_c"] = roundf(l.temperatura * 100) / 100;
    o["umidade_pct"] = roundf(l.umidade * 10) / 10;
    o["co2_ppm"] = l.co2;
  }
  String corpo;
  serializeJson(doc, corpo);

  HTTPClient http;
  http.setConnectTimeout(5000);
  http.setTimeout(TEMPO_HTTP_MS);
  if (!http.begin(AURORA_URL)) {
    Serial.println("[envio] URL inválida em aurora_config.h (AURORA_URL)");
    agendarNovaTentativa(ESPERA_MAX_MS);
    return;
  }
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Dispositivo", AURORA_DISPOSITIVO);
  http.addHeader("X-Chave", AURORA_CHAVE);
  const int status = http.POST(corpo);
  http.end();

  if (status == 200) {
    retirar(n);
    esperaAtualMs = ESPERA_MIN_MS;
    proximoEnvioMs = millis();
    Serial.printf("[envio] %u leitura(s) entregues; restam %u\n", (unsigned)n, (unsigned)quantidade);
  } else if (status == 401 || status == 403) {
    Serial.println("[envio] chave recusada: confira AURORA_DISPOSITIVO e AURORA_CHAVE. Dados guardados.");
    agendarNovaTentativa(ESPERA_CHAVE_MS);
  } else if (status == 400 || status == 413 || status == 422) {
    // O servidor diz que o lote é inválido: reenviar daria o mesmo erro para sempre.
    Serial.printf("[envio] servidor recusou o lote (HTTP %d); descartando %u leitura(s)\n", status,
                  (unsigned)n);
    retirar(n);
  } else {
    // Sem rede, servidor desligado (status < 0) ou erro 5xx: guarda e tenta depois, cada vez
    // esperando mais (5 s, 10 s, 20 s... até 5 min) para não sobrecarregar a rede.
    Serial.printf("[envio] falhou (%d: %s); dados guardados\n", status,
                  status < 0 ? HTTPClient::errorToString(status).c_str() : "erro do servidor");
    agendarNovaTentativa(esperaAtualMs);
    esperaAtualMs = esperaAtualMs * 2 > ESPERA_MAX_MS ? ESPERA_MAX_MS : esperaAtualMs * 2;
  }
}

// ------------------------------------------------------------------ principal

void setup() {
  Serial.begin(115200);
  delay(200);
  iniciarWatchdog();
  snprintf(idBoot, sizeof(idBoot), "%08lx", (unsigned long)esp_random());
  Serial.printf("\n=== Aurora | dispositivo %s | boot %s ===\n", AURORA_DISPOSITIVO, idBoot);

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.setSleep(false);
  cuidarDoWifi();

  if (!iniciarSensor()) falhasSensor = 1;
  ultimaLeituraMs = millis();
}

void loop() {
  esp_task_wdt_reset();
  cuidarDoWifi();

  // Primeira leitura ~6 s após ligar (tempo do SCD4x); depois, a cada INTERVALO_LEITURA_S.
  const uint32_t intervalo = primeiraLeitura ? 6000UL : INTERVALO_LEITURA_S * 1000UL;
  if (jaPassou(ultimaLeituraMs + intervalo)) {
    ultimaLeituraMs = millis();
    primeiraLeitura = false;
    tentarMedir();
  }

  enviarLote();

  static uint32_t ultimoRelatorioMs = 0;
  if (jaPassou(ultimoRelatorioMs + 600000UL)) {  // a cada 10 min
    ultimoRelatorioMs = millis();
    Serial.printf("[status] wifi=%s buffer=%u/%u perdidas=%lu memoria_livre=%u\n",
                  WiFi.status() == WL_CONNECTED ? "ok" : "sem", (unsigned)quantidade,
                  (unsigned)CAPACIDADE_BUFFER, (unsigned long)descartadasPorBufferCheio,
                  (unsigned)ESP.getFreeHeap());
  }
  delay(50);
}
