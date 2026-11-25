"""Small, ephemeral reaction rooms for signed-in browsers on one local instance."""
import hashlib
import re
import secrets
import threading
import time
from dataclasses import dataclass, field

MAX_ROOMS = 24
MAX_STATIONS = 3
JOIN_SECONDS = 300
ROOM_SECONDS = 1800
LEASE_SECONDS = 6
CUE_GRACE_SECONDS = 2
MAX_PRACTICE_MS = 120_000
JOIN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE = re.compile(rf"[{JOIN_ALPHABET}]{{8}}", re.ASCII)
TOKEN = re.compile(r"[A-Za-z0-9_-]{32}", re.ASCII)
WINDOW = 7000
MAX_TICKETS = 360
# Same station order on both turns. These arrays are room-specific because the
# room may have two rather than three stations.
DUEL = {2: (1, 2, 1, 2, 2, 1), 3: (1, 3, 2, 3, 1, 2)}


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


class RoomError(Exception):
    def __init__(self, code, status=409):
        self.code = code
        self.status = status


@dataclass
class Member:
    station: int
    actor: int
    browser: str
    seen: float


@dataclass
class Target:
    ticket: int
    station: int
    offered: float
    acknowledged: float | None = None


@dataclass
class Room:
    code: str
    host: str
    created: float
    members: dict[str, Member]
    phase: str = "lobby"
    mode: str = "practice"
    turn: int = 0
    sequence: tuple[int, ...] = ()
    index: int = 0
    ticket: int = 0
    target: Target | None = None
    scores: list[dict] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)

    def advance(self, now: float) -> None:
        if self.index == len(self.sequence):
            self.target = None
            self.scores.append({
                "hits": sum(row["hit"] for row in self.rows),
                "misses": sum(not row["hit"] for row in self.rows),
                "total": sum(row["ms"] for row in self.rows),
                "rows": self.rows,
            })
            self.phase = "change" if self.mode == "duel" and self.turn == 0 else "complete"
            return
        self.ticket += 1
        self.target = Target(self.ticket, self.sequence[self.index], now)

    def start(self, mode: str, now: float) -> None:
        if self.phase not in {"lobby", "complete"} or len(self.members) < 2:
            raise RoomError("not_ready")
        if self.ticket > MAX_TICKETS - 12:
            raise RoomError("round_limit", 409)
        self.mode = mode
        self.turn = 0
        self.index = 0
        self.rows = []
        self.scores = []
        lineup = sorted(member.station for member in self.members.values())
        # Lobby departures may leave gaps. The duel maps positions onto the
        # *actual* numbered stations then keeps that exact sequence on turn 2.
        self.sequence = tuple(lineup[i - 1] for i in DUEL[len(lineup)]) if mode == "duel" else tuple(lineup)
        self.phase = "playing"
        self.advance(now)

    def next_turn(self, now: float) -> None:
        if self.phase != "change":
            raise RoomError("not_ready")
        self.turn = 1
        self.index = 0
        self.rows = []
        self.phase = "playing"
        self.advance(now)

    def record(self, hit: bool, ms: int, now: float) -> None:
        self.rows.append({"id": self.target.station, "hit": hit, "ms": ms})
        self.index += 1
        self.advance(now)

    def tick_target(self, now: float) -> None:
        target = self.target
        if not target:
            return
        if target.acknowledged is None:
            if now >= target.offered + CUE_GRACE_SECONDS * 2:
                # No proof the station was offered a usable cue; don't score
                # a miss or silently change a duel's station order.
                raise RoomError("station_lost", 410)
        elif self.mode == "duel" and now >= target.acknowledged + WINDOW / 1000 + CUE_GRACE_SECONDS:
            self.record(False, WINDOW, now)
        elif self.mode == "practice" and now >= target.acknowledged + MAX_PRACTICE_MS / 1000:
            raise RoomError("expired", 410)

    def snapshot(self, key: str, member: Member) -> dict:
        target = self.target
        comparison = None
        if len(self.scores) == 2:
            a, b = self.scores
            comparison = (
                (a["hits"] - b["hits"])
                or (b["misses"] - a["misses"])
                or (b["total"] - a["total"])
            )
        return {
            "role": "host" if key == self.host else "station",
            "join_code": self.code if key == self.host and self.phase == "lobby" else None,
            "station": member.station,
            "stations": sorted(current.station for current in self.members.values()),
            "phase": self.phase,
            "mode": self.mode,
            "turn": self.turn + 1,
            "sequence": self.sequence,
            "target": {"station": target.station, "ticket": target.ticket,
                       "window": WINDOW if self.mode == "duel" else 0} if target else None,
            "rows": self.rows[:],
            "scores": self.scores[:],
            "winner": None if comparison is None else (0 if not comparison else 1 if comparison > 0 else 2),
        }


class ReactionRooms:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.rooms: dict[str, Room] = {}
        self.credentials: dict[str, str] = {}
        self.attempts: dict[int, tuple[float, int]] = {}

    def remove(self, room: Room) -> None:
        self.rooms.pop(room.code, None)
        for key in room.members:
            self.credentials.pop(key, None)

    def remove_member(self, room: Room, key: str) -> None:
        self.credentials.pop(key, None)
        room.members.pop(key, None)

    def clean(self, now: float) -> None:
        for room in tuple(self.rooms.values()):
            inactive = [key for key, member in room.members.items() if now - member.seen >= LEASE_SECONDS]
            absolute_expired = now - room.created >= ROOM_SECONDS
            lobby_expired = room.phase == "lobby" and now - room.created >= JOIN_SECONDS
            if absolute_expired or lobby_expired or room.host in inactive or (inactive and room.phase != "lobby"):
                self.remove(room)
            else:
                for key in inactive:
                    self.remove_member(room, key)
                try:
                    room.tick_target(now)
                except RoomError:
                    self.remove(room)
        for actor, (start, _) in tuple(self.attempts.items()):
            if now - start >= 120:
                del self.attempts[actor]

    def count_join(self, actor, now) -> None:
        if actor not in self.attempts and len(self.attempts) >= 512:
            raise RoomError("try_later", 429)
        start, count = self.attempts.get(actor, (now, 0))
        if count >= 12:
            raise RoomError("try_later", 429)
        self.attempts[actor] = (start, count + 1)

    def add_member(self, room: Room, station: int, actor: int, browser: str, now: float) -> str:
        token = secrets.token_urlsafe(24)
        key = digest(token)
        self.credentials[key] = room.code
        room.members[key] = Member(station, actor, browser, now)
        return token

    def enter(self, actor: int, browser: str, code: str | None) -> dict:
        with self.lock:
            now = self.clock()
            self.clean(now)
            if code is not None:
                self.count_join(actor, now)
                room = self.rooms.get(code)
                if not room or room.phase != "lobby":
                    raise RoomError("unavailable", 404)
                used = {member.station for member in room.members.values()}
                station = next((i for i in range(1, MAX_STATIONS + 1) if i not in used), None)
                if station is None:
                    raise RoomError("room_full")
                if any(member.browser == browser for member in room.members.values()):
                    raise RoomError("already_joined")
            else:
                if len(self.rooms) >= MAX_ROOMS or any(
                    room.members[room.host].actor == actor for room in self.rooms.values()
                ):
                    raise RoomError("room_limit", 429)
                code = next((attempt for attempt in self.candidate_codes() if attempt not in self.rooms), None)
                if code is None:
                    raise RoomError("try_later", 429)
                room = Room(code, "", now, {})
                self.rooms[code] = room
                station = 1
            token = self.add_member(room, station, actor, browser, now)
            key = digest(token)
            if not room.host:
                room.host = key
            return {**room.snapshot(key, room.members[key]), "token": token}

    @staticmethod
    def candidate_codes():
        for _ in range(24):
            yield "".join(secrets.choice(JOIN_ALPHABET) for _ in range(8))

    def act(self, actor: int, browser: str, token: str, action: str, body: dict) -> dict:
        with self.lock:
            now = self.clock()
            self.clean(now)
            key = digest(token)
            room = self.rooms.get(self.credentials.get(key))
            member = room.members.get(key) if room else None
            if not member or member.actor != actor or member.browser != browser:
                raise RoomError("room_gone", 410)
            host = key == room.host
            if action in {"start", "next", "stop"} and not host:
                raise RoomError("host_only", 403)
            if action in {"leave", "stop"}:
                if host or room.phase != "lobby":
                    self.remove(room)
                else:
                    self.remove_member(room, key)
                return {"phase": "closed"}
            if action == "start":
                room.start(body["mode"], now)
            elif action == "next":
                room.next_turn(now)
            elif action in {"ack", "press"}:
                self.station_event(room, member, action, body, now)
            member.seen = now
            return room.snapshot(key, member)

    @staticmethod
    def station_event(room: Room, member: Member, action: str, body: dict, now: float) -> None:
        target = room.target
        if not target or body["ticket"] != target.ticket or member.station != target.station:
            raise RoomError("stale")
        if action == "ack":
            if target.acknowledged is not None:
                raise RoomError("stale")
            target.acknowledged = now
            return
        if target.acknowledged is None:
            raise RoomError("stale")
        ms = body["ms"]
        max_ms = WINDOW - 1 if room.mode == "duel" else MAX_PRACTICE_MS
        # Browser time is advisory; deny implausibly larger than time elapsed
        # since the server offered the cue, even when the caller supplies it.
        if ms > max_ms or ms > (now - target.offered) * 1000:
            raise RoomError("stale")
        room.record(True, ms, now)
