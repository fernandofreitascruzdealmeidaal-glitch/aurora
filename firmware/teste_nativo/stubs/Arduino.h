#pragma once
// Imitações mínimas das APIs do ESP32/Arduino para testar o firmware no computador.
#include <cstdint>
#include <cstddef>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <string>
using std::isnan;
using String = std::string;
class Print { public: virtual ~Print() {} virtual size_t write(uint8_t) { return 1; } };
class Stream : public Print { public: virtual int available() { return 0; } virtual int read() { return -1; } };
struct SerialT : Stream { void begin(unsigned long) {} void println(const char*) {} int printf(const char*, ...) __attribute__((format(printf, 2, 3))) { return 0; } };
extern SerialT Serial;
extern uint32_t g_millis;
uint32_t millis();
void delay(uint32_t);
long random(long, long);
uint32_t esp_random();
extern bool g_reiniciou;
struct EspClass { void restart() { g_reiniciou = true; } uint32_t getFreeHeap() { return 0; } };
extern EspClass ESP;
typedef int esp_err_t;
#define ESP_OK 0
