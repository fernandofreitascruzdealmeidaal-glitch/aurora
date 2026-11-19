#pragma once
#include <Arduino.h>
#include <esp_arduino_version.h>
#if ESP_ARDUINO_VERSION_MAJOR >= 3
typedef struct { uint32_t timeout_ms; uint32_t idle_core_mask; bool trigger_panic; } esp_task_wdt_config_t;
esp_err_t esp_task_wdt_init(const esp_task_wdt_config_t*);
esp_err_t esp_task_wdt_reconfigure(const esp_task_wdt_config_t*);
#else
esp_err_t esp_task_wdt_init(uint32_t, bool);
#endif
esp_err_t esp_task_wdt_add(void*);
esp_err_t esp_task_wdt_reset();
