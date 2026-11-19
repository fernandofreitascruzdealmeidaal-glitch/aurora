// Testes do firmware rodando no computador (sem ESP32).
// O main.cpp de verdade é incluído aqui; Wi-Fi, HTTP e relógio são imitações controláveis.
// Rodar: ./rodar.sh

#include "../src/main.cpp"

#include <algorithm>
#include <iostream>

static int falhas = 0;
#define VERIFICA(cond)                                                              \
  do {                                                                              \
    if (!(cond)) {                                                                  \
      std::cerr << "  FALHOU linha " << __LINE__ << ": " #cond << std::endl;        \
      falhas++;                                                                     \
    }                                                                               \
  } while (0)

static void reiniciar(uint32_t inicioMs = 0) {
  g_millis = inicioMs;
  g_wifi_ok = false;
  g_wifi_begins = 0;
  g_reiniciou = false;
  g_http_status = 200;
  g_corpos.clear();
  inicioBuffer = quantidade = 0;
  descartadasPorBufferCheio = 0;
  contadorLeituras = 0;
  ultimaTentativaWifiMs = 0;
  proximoEnvioMs = inicioMs;
  esperaAtualMs = ESPERA_MIN_MS;
  falhasSensor = 0;
  primeiraLeitura = true;
  setup();
}

static void avancar(uint32_t ms) {
  const uint32_t fim = g_millis + ms;
  while ((int32_t)(g_millis - fim) < 0) loop();
}

// Avança muito tempo de uma vez, rodando o loop só de vez em quando (para simular semanas rápido).
static void saltar(uint64_t totalMs, uint32_t passoMs) {
  for (uint64_t feito = 0; feito < totalMs; feito += passoMs) {
    g_millis += passoMs;
    loop();
  }
}

static JsonDocument ultimoEnvio() {
  JsonDocument doc;
  deserializeJson(doc, g_corpos.back());
  return doc;
}

static void teste(const char* nome, void (*f)()) {
  const int antes = falhas;
  f();
  std::cout << (falhas == antes ? "ok    " : "FALHOU") << "  " << nome << std::endl;
}

int main() {
  teste("sem Wi-Fi: guarda as leituras e não tenta enviar", [] {
    reiniciar();
    avancar(10 * 60 * 1000);  // 10 min
    VERIFICA(quantidade == 10);
    VERIFICA(g_corpos.empty());
  });

  teste("Wi-Fi volta: envia tudo e esvazia o buffer", [] {
    reiniciar();
    avancar(10 * 60 * 1000);
    g_wifi_ok = true;
    avancar(1000);
    VERIFICA(quantidade == 0);
    VERIFICA(g_corpos.size() == 1);
    JsonDocument doc = ultimoEnvio();
    JsonArray lista = doc["leituras"];
    VERIFICA(lista.size() == 10);
    VERIFICA(String(lista[0]["id_leitura"].as<const char*>()) == "deadbeef-1");
    // a primeira leitura foi feita aos 6 s; enviada ~10 min depois
    const uint32_t idade = lista[0]["idade_s"];
    VERIFICA(idade >= 590 && idade <= 600);
    VERIFICA(lista[0]["co2_ppm"].as<int>() >= 250);
  });

  teste("muitas leituras: envia em lotes de no máximo 50", [] {
    reiniciar();
    avancar(120 * 60 * 1000);  // 2 h → 120 leituras
    g_wifi_ok = true;
    avancar(1000);
    VERIFICA(quantidade == 0);
    VERIFICA(g_corpos.size() == 3);  // 50 + 50 + 20
  });

  teste("servidor fora do ar: guarda e espera cada vez mais", [] {
    reiniciar();
    g_wifi_ok = true;
    g_http_status = -1;  // conexão recusada
    avancar(7000);       // 1ª leitura aos 6 s, 1ª tentativa logo em seguida
    VERIFICA(g_corpos.size() == 1);
    VERIFICA(quantidade == 1);
    avancar(4000);  // ainda dentro da espera de 5 s
    VERIFICA(g_corpos.size() == 1);
    avancar(2000);  // passou dos 5 s → 2ª tentativa (aos 11 s)
    VERIFICA(g_corpos.size() == 2);
    avancar(7000);  // espera agora é 10 s: aos 20 s ainda não tentou
    VERIFICA(g_corpos.size() == 2);
    avancar(2000);
    VERIFICA(g_corpos.size() == 3);
    g_http_status = 200;  // servidor volta
    avancar(25000);
    VERIFICA(quantidade == 0);
  });

  teste("espera entre tentativas nunca passa de 5 min", [] {
    reiniciar();
    g_wifi_ok = true;
    g_http_status = 503;
    avancar(3 * 3600 * 1000);
    VERIFICA(esperaAtualMs == ESPERA_MAX_MS);
    VERIFICA(quantidade == 180);  // nada perdido
  });

  teste("servidor recusa o lote (422): descarta para não travar para sempre", [] {
    reiniciar();
    g_wifi_ok = true;
    g_http_status = 422;
    avancar(7000);
    VERIFICA(g_corpos.size() == 1);
    VERIFICA(quantidade == 0);
  });

  teste("chave errada (401): guarda os dados e tenta de novo só em 10 min", [] {
    reiniciar();
    g_wifi_ok = true;
    g_http_status = 401;
    avancar(7000);
    VERIFICA(g_corpos.size() == 1);
    VERIFICA(quantidade == 1);
    avancar(9 * 60 * 1000);
    VERIFICA(g_corpos.size() == 1);
    g_http_status = 200;
    avancar(2 * 60 * 1000);
    VERIFICA(quantidade == 0);
  });

  teste("buffer cheio (13 h sem rede): mantém as 720 mais recentes", [] {
    reiniciar();
    avancar(13 * 3600 * 1000);
    VERIFICA(quantidade == CAPACIDADE_BUFFER);
    VERIFICA(descartadasPorBufferCheio == 780 - CAPACIDADE_BUFFER);
    VERIFICA(noBuffer(0).contador == 780 - CAPACIDADE_BUFFER + 1);  // as mais antigas saíram
    VERIFICA(noBuffer(quantidade - 1).contador == 780);
  });

  teste("relógio interno dá a volta (49 dias): nada quebra", [] {
    reiniciar(0xFFFFFFFFu - 5 * 60 * 1000);  // faltam 5 min para millis() voltar a zero
    avancar(10 * 60 * 1000);                 // atravessa a virada
    VERIFICA(quantidade == 10);
    g_wifi_ok = true;
    avancar(1000);
    VERIFICA(quantidade == 0);
    JsonDocument doc = ultimoEnvio();
    const uint32_t idade = doc["leituras"][0]["idade_s"];
    VERIFICA(idade >= 590 && idade <= 600);  // e não 4 bilhões de segundos
  });

  teste("IDs das leituras nunca se repetem", [] {
    reiniciar();
    g_wifi_ok = true;
    avancar(30 * 60 * 1000);
    std::vector<std::string> ids;
    for (const String& corpo : g_corpos) {
      JsonDocument doc;
      deserializeJson(doc, corpo);
      for (JsonObject o : doc["leituras"].as<JsonArray>()) ids.push_back(o["id_leitura"].as<std::string>());
    }
    std::sort(ids.begin(), ids.end());
    VERIFICA(std::adjacent_find(ids.begin(), ids.end()) == ids.end());
    VERIFICA(ids.size() == 30);
  });

  teste("30 dias sem Wi-Fi (férias): quando a rede volta, envia normalmente", [] {
    reiniciar();
    avancar(2000);
    saltar(30ull * 24 * 3600 * 1000, 10 * 60 * 1000);
    VERIFICA(quantidade > 0);
    g_wifi_ok = true;
    avancar(60 * 1000);
    VERIFICA(!g_corpos.empty());
    VERIFICA(quantidade == 0);
  });

  teste("30 dias conectado e a rede cai: tenta reconectar logo", [] {
    reiniciar();
    g_wifi_ok = true;
    avancar(2000);
    saltar(30ull * 24 * 3600 * 1000, 10 * 60 * 1000);
    const int antes = g_wifi_begins;
    g_wifi_ok = false;
    avancar(1000);
    VERIFICA(g_wifi_begins == antes + 1);
    avancar(31 * 1000);  // e de novo a cada 30 s
    VERIFICA(g_wifi_begins == antes + 2);
  });

  teste("leituras impossíveis são barradas no próprio sensor", [] {
    Leitura l{1, 0, 24.0f, 50.0f, 800};
    VERIFICA(valida(l));
    l.co2 = 0;  // o SCD4x devolve 0 quando a medição falha
    VERIFICA(!valida(l));
    l = {1, 0, NAN, 50.0f, 800};
    VERIFICA(!valida(l));
    l = {1, 0, 24.0f, 120.0f, 800};
    VERIFICA(!valida(l));
    l = {1, 0, 24.0f, 50.0f, 65535};
    VERIFICA(!valida(l));
  });

  std::cout << (falhas ? "\nHÁ FALHAS\n" : "\nTodos os testes do firmware passaram.\n");
  return falhas ? 1 : 0;
}
