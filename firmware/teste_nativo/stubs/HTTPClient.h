#pragma once
#include <Arduino.h>
#include <vector>
extern int g_http_status;
extern std::vector<String> g_corpos;
class HTTPClient { public: void setConnectTimeout(int32_t) {} void setTimeout(uint16_t) {} bool begin(String) { return true; } void addHeader(const String&, const String&, bool = false, bool = true) {} int POST(String corpo) { g_corpos.push_back(corpo); return g_http_status; } void end() {} static String errorToString(int) { return "x"; } };
