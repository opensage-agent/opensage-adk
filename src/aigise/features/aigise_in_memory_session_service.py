from __future__ import annotations

import copy
import logging
import time
import uuid
from typing import Any, Optional

from google.adk.events.event import Event
from google.adk.sessions.base_session_service import (
    BaseSessionService,
    GetSessionConfig,
    ListSessionsResponse,
)
from google.adk.sessions.in_memory_session_service import AlreadyExistsError
from google.adk.sessions.session import Session

logger = logging.getLogger("opensage." + __name__)


class OpenSageInMemorySessionService(BaseSessionService):
    """In-memory SessionService without deepcopy and without app/user state.

    Differences vs google.adk.sessions.in_memory_session_service.InMemorySessionService:
    - No deepcopy on reads; callers receive and mutate the live Session object.
    - No app_state/user_state accumulation or merging; only per-session state.
    - GetSessionConfig is ignored (we do not slice events on get).
    - One live Session object instance per (app_name, user_id, session_id).
    """

    def __init__(self):
        # sessions[app_name][user_id][session_id] = Session (live object)
        self.sessions: dict[str, dict[str, dict[str, Session]]] = {}

    async def create_session(
        self,
        *,
        app_name: str,
        user_id: str,
        state: Optional[dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> Session:
        return self._create_session_impl(
            app_name=app_name,
            user_id=user_id,
            state=state,
            session_id=session_id,
        )

    def create_session_sync(
        self,
        *,
        app_name: str,
        user_id: str,
        state: Optional[dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> Session:
        logger.warning("Deprecated. Please migrate to the async method.")
        return self._create_session_impl(
            app_name=app_name,
            user_id=user_id,
            state=state,
            session_id=session_id,
        )

    async def get_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        config: Optional[GetSessionConfig] = None,
    ) -> Optional[Session]:
        # Ignore config; return LIVE object
        return self._get_session_impl(
            app_name=app_name, user_id=user_id, session_id=session_id
        )

    def get_session_sync(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        config: Optional[GetSessionConfig] = None,
    ) -> Optional[Session]:
        return self._get_session_impl(
            app_name=app_name, user_id=user_id, session_id=session_id
        )

    async def append_event(self, session: Session, event: Event) -> Event:
        # Ensure we append to the canonical stored object
        storage_session = self._get_session_impl(
            app_name=session.app_name, user_id=session.user_id, session_id=session.id
        )
        if storage_session is None:
            return event

        # Let base class update the session (content, state_delta, etc.) on the LIVE object.
        await super().append_event(session=storage_session, event=event)

        storage_session.last_update_time = event.timestamp
        return event

    async def list_sessions(
        self, *, app_name: str, user_id: Optional[str] = None
    ) -> ListSessionsResponse:
        return self._list_sessions_impl(app_name=app_name, user_id=user_id)

    def list_sessions_sync(
        self, *, app_name: str, user_id: Optional[str] = None
    ) -> ListSessionsResponse:
        logger.warning("Deprecated. Please migrate to the async method.")
        return self._list_sessions_impl(app_name=app_name, user_id=user_id)

    async def delete_session(
        self, *, app_name: str, user_id: str, session_id: str
    ) -> None:
        self._delete_session_impl(
            app_name=app_name, user_id=user_id, session_id=session_id
        )

    def delete_session_sync(
        self, *, app_name: str, user_id: str, session_id: str
    ) -> None:
        self._delete_session_impl(
            app_name=app_name, user_id=user_id, session_id=session_id
        )

    # Internal helpers
    def _create_session_impl(
        self,
        *,
        app_name: str,
        user_id: str,
        state: Optional[dict[str, Any]],
        session_id: Optional[str],
    ) -> Session:
        if session_id and self._get_session_impl(
            app_name=app_name, user_id=user_id, session_id=session_id
        ):
            raise AlreadyExistsError(f"Session with id {session_id} already exists.")

        sid = (
            session_id.strip()
            if session_id and session_id.strip()
            else str(uuid.uuid4())
        )
        session = Session(
            app_name=app_name,
            user_id=user_id,
            id=sid,
            state=state or {},
            last_update_time=time.time(),
        )
        self.sessions.setdefault(app_name, {}).setdefault(user_id, {})[sid] = session
        return session

    def _get_session_impl(
        self, *, app_name: str, user_id: str, session_id: str
    ) -> Optional[Session]:
        if app_name not in self.sessions:
            return None
        if user_id not in self.sessions[app_name]:
            return None
        return self.sessions[app_name][user_id].get(session_id)

    def _list_sessions_impl(
        self, *, app_name: str, user_id: Optional[str]
    ) -> ListSessionsResponse:
        empty_response = ListSessionsResponse()
        if app_name not in self.sessions:
            return empty_response
        if user_id is not None and user_id not in self.sessions[app_name]:
            return empty_response

        sessions_without_events = []

        if user_id is None:
            for user_id in self.sessions[app_name]:
                for session_id in self.sessions[app_name][user_id]:
                    session = self.sessions[app_name][user_id][session_id]
                    copied_session = copy.deepcopy(session)
                    copied_session.events = []
                    sessions_without_events.append(copied_session)
        else:
            for session in self.sessions[app_name][user_id].values():
                copied_session = copy.deepcopy(session)
                copied_session.events = []
                sessions_without_events.append(copied_session)
        return ListSessionsResponse(sessions=sessions_without_events)

    def _delete_session_impl(
        self, *, app_name: str, user_id: str, session_id: str
    ) -> None:
        if app_name not in self.sessions:
            return
        if user_id not in self.sessions[app_name]:
            return
        self.sessions[app_name][user_id].pop(session_id, None)
        if not self.sessions[app_name][user_id]:
            self.sessions[app_name].pop(user_id, None)
        if not self.sessions[app_name]:
            self.sessions.pop(app_name, None)
