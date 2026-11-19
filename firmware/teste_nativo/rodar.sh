#!/usr/bin/env bash
# Compila e roda os testes do firmware no computador (precisa de g++ e git).
set -euo pipefail
cd "$(dirname "$0")"

LIBS=.libs
if [ ! -d "$LIBS/ArduinoJson" ]; then
  mkdir -p "$LIBS"
  git -c advice.detachedHead=false clone --quiet --depth 1 --branch v7.2.1 https://github.com/bblanchon/ArduinoJson.git "$LIBS/ArduinoJson"
fi

for versao in 2 3; do
  padrao=gnu++11
  [ "$versao" = 3 ] && padrao=gnu++2a
  # -Wno-maybe-uninitialized: falso alarme conhecido do GCC dentro da ArduinoJson
  echo "== núcleo ESP32 Arduino ${versao}.x (${padrao})"
  g++ -std=$padrao -Wall -Wextra -Werror -Wno-maybe-uninitialized -O1 -g \
      -fsanitize=address,undefined -fno-sanitize-recover=all \
      -DESP_ARDUINO_VERSION_MAJOR=$versao -DAURORA_USAR_EXEMPLO=1 -DAURORA_FORCAR_SIMULACAO=1 \
      -Istubs -isystem "$LIBS/ArduinoJson/src" -I../include \
      teste.cpp stubs/stubs.cpp -o "$LIBS/teste_$versao"
  "$LIBS/teste_$versao"
done
