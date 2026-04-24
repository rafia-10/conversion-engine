"""
conversation_manager.py — Per-contact thread state manager.

Thread isolation is enforced: thread_id = hash(contact_email).
Two people at the same company have different thread_ids and the manager
never crosses context between them.
"""
import hashlib
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

THREADS_DIR = Path(os.getenv("THREADS_DIR", "outputs/threads"))


class ConversationManager:
    def __init__(self):
        THREADS_DIR.mkdir(parents=True, exist_ok=True)

    def _thread_path(self, thread_id: str) -> Path:
        return THREADS_DIR / f"{thread_id}.json"

    def _make_thread_id(self, contact_email: str) -> str:
        return hashlib.sha256(contact_email.lower().strip().encode()).hexdigest()[:16]

    def get_thread(self, contact_email: str) -> Dict:
        """Load or create a thread for a contact. Never shares across contacts."""
        thread_id = self._make_thread_id(contact_email)
        path = self._thread_path(thread_id)

        if path.exists():
            try:
                with path.open(encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load thread {thread_id}: {e}")

        # New thread
        thread = {
            "thread_id": thread_id,
            "contact_email": contact_email,
            "contact_id": None,
            "company_id": None,
            "channel": "email",
            "qualification_state": "new",
            "segment": None,
            "segment_confidence": 0.0,
            "messages": [],
            "created_at": datetime.utcnow().isoformat() + "Z",
            "updated_at": datetime.utcnow().isoformat() + "Z",
            "metadata": {},
        }
        self._save_thread(thread)
        return thread

    def _save_thread(self, thread: Dict) -> None:
        thread["updated_at"] = datetime.utcnow().isoformat() + "Z"
        path = self._thread_path(thread["thread_id"])
        with path.open("w", encoding="utf-8") as f:
            json.dump(thread, f, indent=2)

    def append_message(self, thread_id: str, role: str, content: str,
                       metadata: Optional[Dict] = None) -> None:
        """Add a message to the thread. role: 'agent' | 'prospect' | 'system'."""
        path = self._thread_path(thread_id)
        if not path.exists():
            logger.warning(f"Thread {thread_id} not found; skipping append")
            return

        with path.open(encoding="utf-8") as f:
            thread = json.load(f)

        thread["messages"].append({
            "role": role,
            "content": content,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "metadata": metadata or {},
        })
        self._save_thread(thread)

    def get_context(self, thread_id: str) -> List[Dict]:
        """Return only this thread's message history — never another thread's."""
        path = self._thread_path(thread_id)
        if not path.exists():
            return []
        with path.open(encoding="utf-8") as f:
            thread = json.load(f)
        return thread.get("messages", [])

    def update_qualification(self, thread_id: str, segment: str,
                             confidence: float, state: str) -> None:
        path = self._thread_path(thread_id)
        if not path.exists():
            return
        with path.open(encoding="utf-8") as f:
            thread = json.load(f)
        thread["segment"] = segment
        thread["segment_confidence"] = confidence
        thread["qualification_state"] = state
        self._save_thread(thread)

    def set_channel(self, thread_id: str, channel: str) -> None:
        """channel: 'email' | 'sms' | 'voice'"""
        path = self._thread_path(thread_id)
        if not path.exists():
            return
        with path.open(encoding="utf-8") as f:
            thread = json.load(f)
        thread["channel"] = channel
        self._save_thread(thread)

    def mark_booked(self, thread_id: str, booking_url: Optional[str] = None) -> None:
        path = self._thread_path(thread_id)
        if not path.exists():
            return
        with path.open(encoding="utf-8") as f:
            thread = json.load(f)
        thread["qualification_state"] = "discovery_call_booked"
        if booking_url:
            thread["metadata"]["booking_url"] = booking_url
        self._save_thread(thread)

    def list_threads(self) -> List[Dict]:
        threads = []
        for p in THREADS_DIR.glob("*.json"):
            try:
                with p.open(encoding="utf-8") as f:
                    t = json.load(f)
                    threads.append({
                        "thread_id": t["thread_id"],
                        "contact_email": t["contact_email"],
                        "state": t["qualification_state"],
                        "channel": t["channel"],
                        "messages": len(t["messages"]),
                        "updated_at": t["updated_at"],
                    })
            except Exception:
                pass
        return sorted(threads, key=lambda x: x["updated_at"], reverse=True)
