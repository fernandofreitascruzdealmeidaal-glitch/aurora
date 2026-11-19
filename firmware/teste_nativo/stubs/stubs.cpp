// Implementação das imitações, com estado que o teste controla.
#include <Arduino.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <Wire.h>
#include <esp_task_wdt.h>

uint32_t g_millis = 0;
bool g_wifi_ok = false;
int g_wifi_begins = 0;
bool g_reiniciou = false;
int g_http_status = 200;
std::vector<String> g_corpos;

SerialT Serial;
TwoWire Wire;
WiFiClass WiFi;
EspClass ESP;

uint32_t millis() { return g_millis; }
void delay(uint32_t ms) { g_millis += ms; }
long random(long, long) { return 0; }
uint32_t esp_random() { return 0xdeadbeef; }
#if ESP_ARDUINO_VERSION_MAJOR >= 3
esp_err_t esp_task_wdt_init(const esp_task_wdt_config_t*) { return ESP_OK; }
esp_err_t esp_task_wdt_reconfigure(const esp_task_wdt_config_t*) { return ESP_OK; }
#else
esp_err_t esp_task_wdt_init(uint32_t, bool) { return ESP_OK; }
#endif
esp_err_t esp_task_wdt_add(void*) { return ESP_OK; }
esp_err_t esp_task_wdt_reset() { return ESP_OK; }
