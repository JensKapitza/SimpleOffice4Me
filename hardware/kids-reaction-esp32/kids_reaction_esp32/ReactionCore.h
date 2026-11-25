#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string.h>

// Firmware logic shared by the Arduino sketch and the host timing tests.
namespace reaction {
constexpr uint32_t DebounceMs = 25;
constexpr uint32_t WatchdogMs = 2000;
constexpr size_t LineLimit = 96;
constexpr size_t StationCount = 3;

enum class Event { Ready, Pong, Target, Hit, Wrong, Miss };

struct Host {
  virtual uint32_t light(uint8_t station, uint32_t now) = 0;  // zero: all LEDs off
  virtual void emit(Event, const char*, uint32_t, uint8_t, uint32_t, uint32_t) = 0;
  virtual ~Host() = default;
};

struct Button {
  uint32_t changed = 0;
  bool raw = false;
  bool stable = false;

  bool sample(bool down, uint32_t now) {
    if (raw != down) {
      raw = down;
      changed = now;
    }
    if (raw == stable || now - changed < DebounceMs) return false;
    stable = raw;
    return stable;
  }
};

inline bool validEpoch(const char* value) {
  if (!value || strlen(value) != 16) return false;
  for (; *value; ++value) {
    if (!((*value >= 'a' && *value <= 'f') || (*value >= '0' && *value <= '9'))) return false;
  }
  return true;
}

inline bool parseU32(const char* text, uint32_t& value) {
  if (!text || !*text || (text[0] == '0' && text[1])) return false;
  value = 0;
  for (; *text; ++text) {
    if (*text < '0' || *text > '9') return false;
    const uint32_t digit = static_cast<uint32_t>(*text - '0');
    if (value > (UINT32_MAX - digit) / 10U) return false;
    value = value * 10U + digit;
  }
  return true;
}

class Engine {
 public:
  explicit Engine(Host& host) : host_(host) {}

  void receive(char* line, uint32_t now, const bool (&down)[StationCount]) {
    tick(now, down);
    char* next = nullptr;
    const char* command = strtok_r(line, " ", &next);
    const char* epoch = strtok_r(nullptr, " ", &next);
    if (!command || !validEpoch(epoch)) return;
    if (strcmp(command, "HELLO") == 0 && !strtok_r(nullptr, " ", &next)) {
      if (strcmp(epoch_, epoch) == 0) {
        if (linked_ && lastTicket_ == 0) host_.emit(Event::Ready, epoch_, 0, 0, 0, 0);
        return;
      }
      for (const auto& previous : retiredEpochs_) {
        if (strcmp(previous, epoch) == 0) return;
      }
      if (*epoch_) {
        memcpy(retiredEpochs_[retiredCursor_], epoch_, sizeof(epoch_));
        retiredCursor_ = (retiredCursor_ + 1U) % 4U;
      }
      failSafe();
      memcpy(epoch_, epoch, sizeof(epoch_));
      linked_ = true;
      lastContact_ = now;
      lastTicket_ = pingSeq_ = pressSeq_ = 0;
      host_.emit(Event::Ready, epoch_, 0, 0, 0, 0);
      return;
    }
    if (!linked_ || strcmp(epoch_, epoch) != 0) return;
    if (strcmp(command, "STOP") == 0 && !strtok_r(nullptr, " ", &next)) {
      stop();
      return;
    }
    if (strcmp(command, "KEEP") == 0) {
      uint32_t sequence;
      if (parseU32(strtok_r(nullptr, " ", &next), sequence)
          && !strtok_r(nullptr, " ", &next) && sequence > pingSeq_) {
        pingSeq_ = sequence;
        lastContact_ = now;
        host_.emit(Event::Pong, epoch_, sequence, 0, 0, 0);
      }
      return;
    }
    if (strcmp(command, "ARM") != 0 || active_) return;
    uint32_t ticket, station, duration;
    if (!parseU32(strtok_r(nullptr, " ", &next), ticket) || ticket <= lastTicket_
        || !parseU32(strtok_r(nullptr, " ", &next), station) || station < 1 || station > 3
        || !parseU32(strtok_r(nullptr, " ", &next), duration)
        || (duration && (duration < 4000 || duration > 8000))
        || strtok_r(nullptr, " ", &next)) return;
    lastTicket_ = ticket;
    target_ = static_cast<uint8_t>(station);
    duration_ = duration;
    for (size_t i = 0; i < StationCount; ++i) needsRelease_[i] = buttons_[i].raw || buttons_[i].stable;
    active_ = true;
    start_ = host_.light(target_, now);
    host_.emit(Event::Target, epoch_, lastTicket_, target_, duration_, 0);
  }

  void tick(uint32_t now, const bool (&down)[StationCount]) {
    if (linked_ && now - lastContact_ >= WatchdogMs) failSafe();
    bool pressed[StationCount] = {};
    for (size_t i = 0; i < StationCount; ++i) {
      pressed[i] = buttons_[i].sample(down[i], now);
      if (!buttons_[i].raw && !buttons_[i].stable && now - buttons_[i].changed >= DebounceMs) {
        needsRelease_[i] = false;
      }
    }
    if (!active_) return;
    const uint32_t elapsed = now - start_;
    if (duration_ && elapsed >= duration_) {
      stop();
      host_.emit(Event::Miss, epoch_, lastTicket_, target_, 0, 0);
      return;
    }
    // Report a simultaneous wrong edge before the target hit.
    for (size_t offset = 0; offset < StationCount; ++offset) {
      const uint8_t id = static_cast<uint8_t>((target_ + offset) % StationCount + 1);
      if (!pressed[id - 1] || needsRelease_[id - 1]) continue;
      if (id == target_) stop();
      host_.emit(id == target_ ? Event::Hit : Event::Wrong, epoch_, lastTicket_, id, elapsed, ++pressSeq_);
    }
  }

  void failSafe() {
    linked_ = false;
    stop();
  }

 private:
  void stop() {
    active_ = false;
    host_.light(0, 0);
  }

  Host& host_;
  Button buttons_[StationCount] = {};
  bool needsRelease_[StationCount] = {};
  char epoch_[17] = {};
  char retiredEpochs_[4][17] = {};
  size_t retiredCursor_ = 0;
  bool linked_ = false;
  bool active_ = false;
  uint8_t target_ = 0;
  uint32_t lastContact_ = 0;
  uint32_t lastTicket_ = 0;
  uint32_t pingSeq_ = 0;
  uint32_t pressSeq_ = 0;
  uint32_t start_ = 0;
  uint32_t duration_ = 0;
};
}
