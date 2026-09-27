from enum import Enum, auto
from typing import Optional


class MarketState(Enum):
    OPEN = auto()
    CLOSED = auto()


class VirtualClock:
    def __init__(self):
        self.current_tick = 0
        self.day_tick = 0
        self.day_count = 1
        self.state = MarketState.CLOSED

        self.OPEN_DURATION = 27
        self.CLOSED_DURATION = 2
        self.TICK_PER_DAY = self.OPEN_DURATION + self.CLOSED_DURATION

    def _refresh_state(self):
        if 0 <= self.day_tick < self.OPEN_DURATION:
            self.state = MarketState.OPEN
        else:
            self.state = MarketState.CLOSED

    def tick(self):
        self.current_tick += 1
        self.day_tick += 1

        if self.day_tick >= self.TICK_PER_DAY:
            self.day_tick = 0
            self.day_count += 1

        self._refresh_state()

    def restore(self, current_tick: int, day_tick: int, day_count: int, phase: Optional[str] = None):
        self.current_tick = max(0, int(current_tick))
        raw_day_tick = max(0, int(day_tick))
        normalized_day_tick = raw_day_tick % self.TICK_PER_DAY

        if phase == "CLOSED" and normalized_day_tick < self.OPEN_DURATION:
            normalized_day_tick = self.OPEN_DURATION + (raw_day_tick % max(1, self.CLOSED_DURATION))
        elif phase == "OPEN" and normalized_day_tick >= self.OPEN_DURATION:
            normalized_day_tick = raw_day_tick % self.OPEN_DURATION

        self.day_tick = normalized_day_tick
        self.day_count = max(1, int(day_count))

        self._refresh_state()

    def get_virtual_time(self) -> str:
        if self.state == MarketState.OPEN:
            hour = 9 + (self.day_tick // 6)
            minute = (self.day_tick % 6) * 10
            return f"Day {self.day_count} | Market OPEN | {hour:02d}:{minute:02d}"

        relative_closed_tick = max(0, self.day_tick - self.OPEN_DURATION)
        hour = 13 + relative_closed_tick
        if hour >= 24:
            hour -= 24
        return f"Day {self.day_count} | Market CLOSED | {hour:02d}:30"

    def __str__(self):
        return f"[{self.get_virtual_time()}] Total Tick: {self.current_tick}, Day Tick: {self.day_tick}"


if __name__ == "__main__":
    clock = VirtualClock()
    print("Starting AI Agentic Society Virtual Clock Test...")
    print("-" * 50)

    for _ in range(clock.TICK_PER_DAY):
        action = "[MarketEngine] matching trades..." if clock.state == MarketState.OPEN else "[SocialEngine] agents are posting..."
        print(f"{clock} -> {action}")
        clock.tick()

    print("-" * 50)
    print(f"Simulation Day {clock.day_count - 1} completed successfully.")
