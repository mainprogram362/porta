"""Process-wide presence leases shared by PORTA windows and entrypoints."""
from __future__ import annotations

from runtime.process_registry import ProcessRegistry, get_registry, list_processes


class InstancePresence:
    def __init__(self, *, role: str = 'main', screen: str = 'メインメニュー',
                 state: str = '待機中', registry: ProcessRegistry | None = None) -> None:
        self.registry = registry or get_registry()
        self.instance_id = self.registry.identity.key
        self.path = self.registry.path_for(self.registry.identity)
        self._screen, self._state = screen, state
        self._token = self.registry.acquire(role, screen, state)
        self._closed = False

    def update(self, screen: str, state: str) -> None:
        if not self._closed:
            self._screen, self._state = screen, state
            self.registry.update(self._token, screen, state)

    def heartbeat(self) -> None:
        if not self._closed:
            self.registry.update(self._token, self._screen, self._state, pulse=True)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self.registry.release(self._token)


def live_instances() -> list[dict[str, object]]:
    return [dict(pid=item.pid, instance_id=item.identity.key, screen=item.screen,
                 state=item.state, role=item.role, health=item.health)
            for item in list_processes() if item.role in {'main', 'records'}]
