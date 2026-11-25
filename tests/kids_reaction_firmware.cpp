#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <vector>

#include "../hardware/kids-reaction-esp32/kids_reaction_esp32/ReactionCore.h"

using reaction::Event;
struct Frame { Event type; uint32_t ticket; uint8_t id; uint32_t value; uint32_t seq; };
struct Device : reaction::Host {
  uint8_t lit = 0;
  std::vector<Frame> frames;
  uint32_t light(uint8_t id, uint32_t now) override { lit = id; return now; }
  void emit(Event type, const char*, uint32_t ticket, uint8_t id, uint32_t value, uint32_t seq) override {
    frames.push_back({type, ticket, id, value, seq});
  }
};
static const char* epoch = "aabbccddeeff0011";
static const char* fresh = "1122334455667788";

void receive(reaction::Engine& engine, uint32_t time, const bool (&down)[3],
             const char* verb, const char* nonce, const char* tail = "") {
  char buf[97];
  snprintf(buf, sizeof(buf), "%s %s%s", verb, nonce, tail);
  engine.receive(buf, time, down);
}

int main() {
  Device board;
  reaction::Engine engine(board);
  bool down[3] = {};
  receive(engine, 100, down, "ARM", epoch, " 1 1 0");
  assert(board.lit == 0);
  receive(engine, 100, down, "HELLO", epoch);
  receive(engine, 110, down, "ARM", epoch, " 1 1 3000");
  assert(board.lit == 0);  // no too-fast target windows
  down[0] = true;
  engine.tick(115, down);
  receive(engine, 120, down, "ARM", epoch, " 1 1 0");
  assert(board.lit == 1);
  engine.tick(150, down);
  assert(board.frames.back().type == Event::Target);
  down[0] = false;
  engine.tick(155, down); // release only when stable
  engine.tick(179, down);
  engine.tick(180, down);
  down[0] = true;
  engine.tick(190, down);
  down[0] = false;
  engine.tick(197, down);
  down[0] = true;
  engine.tick(202, down);
  engine.tick(226, down);
  assert(board.lit == 1 && board.frames.back().type == Event::Target);
  engine.tick(227, down);
  assert(board.lit == 0 && board.frames.back().type == Event::Hit);
  assert(board.frames.back().value == 107 && board.frames.back().ticket == 1 && board.frames.back().seq == 1);
  const auto count = board.frames.size();
  engine.tick(240, down);
  receive(engine, 241, down, "ARM", epoch, " 1 1 0");
  assert(board.frames.size() == count && board.lit == 0);
  receive(engine, 242, down, "ARM", epoch, " 2 1 4000");
  engine.tick(300, down); // held from previous hit
  assert(board.lit == 1 && board.frames.back().type == Event::Target);
  down[1] = true;
  engine.tick(320, down);
  engine.tick(345, down);
  assert(board.lit == 1 && board.frames.back().type == Event::Wrong && board.frames.back().id == 2);
  receive(engine, 390, down, "KEEP", epoch, " 2");
  receive(engine, 2200, down, "KEEP", epoch, " 2"); // stale ping doesn't extend watchdog
  engine.tick(2390, down);
  assert(board.lit == 0);
  receive(engine, 2400, down, "HELLO", epoch); // reconnect must use a new session
  receive(engine, 2401, down, "ARM", epoch, " 3 3 4000");
  assert(board.lit == 0);
  receive(engine, UINT32_MAX - 45, down, "HELLO", fresh);
  receive(engine, UINT32_MAX - 30, down, "ARM", fresh, " 1 3 4000");
  down[2] = true;
  engine.tick(UINT32_MAX - 10, down);
  engine.tick(17, down);
  assert(board.frames.back().type == Event::Hit && board.frames.back().value == 48);
  receive(engine, 49, down, "HELLO", epoch); // an earlier epoch cannot replace the new one
  receive(engine, 50, down, "ARM", fresh, " 2 3 4000");
  assert(board.lit == 3);
  for (int p = 1; p <= 4; ++p) {
    char ping[8];
    snprintf(ping, sizeof(ping), " %d", p);
    receive(engine, 900U * p, down, "KEEP", fresh, ping);
  }
  engine.tick(4050, down);
  assert(board.lit == 0 && board.frames.back().type == Event::Miss);
  assert(board.frames.back().ticket == 2 && board.frames.back().id == 3);
  puts("Firmware core: pre-arm hold, bounce, edge timing, duplicates, wrong input, watchdog, wrap, timeout verified.");
}
