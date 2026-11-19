#pragma once
#include <Arduino.h>
enum wl_status_t { WL_IDLE_STATUS, WL_CONNECTED };
enum wifi_mode_t { WIFI_STA };
extern bool g_wifi_ok;
extern int g_wifi_begins;
struct WiFiClass { wl_status_t status() { return g_wifi_ok ? WL_CONNECTED : WL_IDLE_STATUS; } bool mode(wifi_mode_t) { return true; } bool setAutoReconnect(bool) { return true; } bool setSleep(bool) { return true; } bool disconnect(bool = false) { return true; } wl_status_t begin(const char*, const char*) { g_wifi_begins++; return WL_IDLE_STATUS; } };
extern WiFiClass WiFi;
