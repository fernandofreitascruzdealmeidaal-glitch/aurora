#pragma once
#include <Arduino.h>
class TwoWire : public Stream { public: bool begin(int, int) { return true; } void beginTransmission(uint8_t) {} uint8_t endTransmission(bool = true) { return 0; } size_t requestFrom(uint8_t, size_t, bool = true) { return 0; } };
extern TwoWire Wire;
