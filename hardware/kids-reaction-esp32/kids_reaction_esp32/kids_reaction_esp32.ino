#include <Arduino.h>
#include <inttypes.h>
#include <stdio.h>
#include "ReactionCore.h"

constexpr uint8_t LedGpio[] = {25, 26, 27};
constexpr uint8_t ButtonGpio[] = {32, 33, 23};

class Board final : public reaction::Host {
 public:
  bool overflow = false;

  uint32_t light(uint8_t station, uint32_t) override {
    for (size_t i = 0; i < reaction::StationCount; ++i) {
      digitalWrite(LedGpio[i], station == i + 1 ? HIGH : LOW);
    }
    return millis();
  }

  void emit(reaction::Event event, const char* epoch, uint32_t ticket, uint8_t id,
            uint32_t value, uint32_t sequence) override {
    using reaction::Event;
    char line[reaction::LineLimit + 1];
    int bytes = 0;
    switch (event) {
      case Event::Ready: bytes = snprintf(line, sizeof(line), "READY %s 1\n", epoch); break;
      case Event::Pong: bytes = snprintf(line, sizeof(line), "PONG %s %" PRIu32 "\n", epoch, ticket); break;
      case Event::Target: bytes = snprintf(line, sizeof(line), "TARGET %s %" PRIu32 " %u %" PRIu32 "\n",
                                          epoch, ticket, static_cast<unsigned>(id), value); break;
      case Event::Miss: bytes = snprintf(line, sizeof(line), "MISS %s %" PRIu32 " %u\n",
                                        epoch, ticket, static_cast<unsigned>(id)); break;
      case Event::Hit:
      case Event::Wrong:
        bytes = snprintf(line, sizeof(line), "%s %s %" PRIu32 " %u %" PRIu32 " %" PRIu32 "\n",
                         event == Event::Hit ? "HIT" : "WRONG", epoch, ticket,
                         static_cast<unsigned>(id), value, sequence);
        break;
    }
    if (bytes <= 0 || bytes >= static_cast<int>(sizeof(line)) || Serial.availableForWrite() < bytes) {
      overflow = true;
      return;
    }
    Serial.write(reinterpret_cast<const uint8_t*>(line), static_cast<size_t>(bytes));
  }
};

Board board;
reaction::Engine engine(board);
char command[reaction::LineLimit + 1] = {};
size_t cursor = 0;
bool drop = false;

void setup() {
  for (size_t i = 0; i < reaction::StationCount; ++i) {
    pinMode(LedGpio[i], OUTPUT);
    digitalWrite(LedGpio[i], LOW);
    pinMode(ButtonGpio[i], INPUT_PULLUP);
  }
  board.light(0, 0);
  Serial.begin(115200);
}

void sample(bool (&down)[reaction::StationCount]) {
  for (size_t i = 0; i < reaction::StationCount; ++i) down[i] = digitalRead(ButtonGpio[i]) == LOW;
}

void loop() {
  bool down[reaction::StationCount];
  sample(down);
  engine.tick(millis(), down);
  // Keep watchdog running under serial noise. Do not block waiting for a newline.
  for (unsigned consumed = 0; consumed < 64 && Serial.available() > 0; ++consumed) {
    const int byte = Serial.read();
    if (byte == '\n') {
      if (!drop && cursor) {
        command[cursor] = '\0';
        sample(down);
        engine.receive(command, millis(), down);
      }
      cursor = 0;
      drop = false;
    } else if (byte != '\r' && !drop) {
      if (byte < 32 || byte > 126 || cursor >= reaction::LineLimit) drop = true;
      else command[cursor++] = static_cast<char>(byte);
    }
    if (board.overflow) break;
  }
  if (board.overflow) {
    board.overflow = false;
    engine.failSafe();
    cursor = 0;
    drop = true;
  }
}
