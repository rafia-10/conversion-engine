from typing import Any, Callable, Dict, List


class EventRegistry:
    _instance = None
    _callbacks: Dict[str, List[Callable]] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(EventRegistry, cls).__new__(cls)
            cls._callbacks = {}
        return cls._instance

    def on(self, event_type: str, callback: Callable = None):
        """Register a callback for an event type. Can be used as a decorator."""
        if callback is None:
            def decorator(cb):
                self.on(event_type, cb)
                return cb
            return decorator

        if event_type not in self._callbacks:
            self._callbacks[event_type] = []
        self._callbacks[event_type].append(callback)

    def trigger(self, event_type: str, data: Any):
        """Trigger all callbacks registered for an event type."""
        callbacks = self._callbacks.get(event_type, [])
        for callback in callbacks:
            try:
                callback(data)
            except Exception as e:
                print(f"Error in callback for {event_type}: {e}")


# Singleton instance
events = EventRegistry()
