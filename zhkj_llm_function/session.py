"""会话存储：按 agent 的 ttl 管理会话有效期，会话内复用同一个引擎实例。

会话状态只存在于内存中：进程重启即失效。
需要跨重启保留历史时，请由业务侧自行保存/恢复（如把 session_id 映射到外部存储）。
"""

import time
import uuid
from typing import Any, Dict, List, Optional


class Session(object):
    """一次会话的完整状态。"""

    __slots__ = ("session_id", "agent_name", "messages", "created_at", "expires_at", "meta", "agent")

    def __init__(
        self,
        session_id: str,
        agent_name: str,
        messages: List[Dict[str, Any]],
        ttl: int,
        meta: Optional[Dict[str, Any]] = None,
        agent: Any = None,
    ) -> None:
        self.session_id = session_id
        self.agent_name = agent_name
        self.messages = messages
        self.created_at = time.time()
        self.expires_at = self.created_at + ttl
        self.meta: Dict[str, Any] = meta or {}
        self.agent = agent

    @property
    def expired(self) -> bool:
        """会话是否已过期。"""
        return time.time() >= self.expires_at

    def touch(self, ttl: int) -> None:
        """续期。"""
        self.expires_at = time.time() + ttl


class SessionStore(object):
    """内存会话存储。"""

    def __init__(self) -> None:
        self._sessions: Dict[str, Session] = {}

    def get(self, session_id: str) -> Optional[Session]:
        """获取未过期会话；已过期返回 None（保留记录以便识别“需重置”）。"""
        session = self._sessions.get(session_id)
        if session is None or session.expired:
            return None
        return session

    def peek(self, session_id: str) -> Optional[Session]:
        """获取会话（不过期检查），用于识别“已过期需重置”的会话。"""
        return self._sessions.get(session_id)

    def create(
        self,
        agent_name: str,
        ttl: int,
        session_id: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        agent: Any = None,
    ) -> Session:
        """新建会话。"""
        sid = session_id or uuid.uuid4().hex
        session = Session(sid, agent_name, list(messages or []), ttl, agent=agent)
        self._sessions[sid] = session
        return session

    def list_session_ids(self) -> List[str]:
        """当前进程内已知的 session_id 列表。"""
        return sorted(self._sessions.keys())

    def reset(
        self,
        session_id: str,
        agent_name: str,
        ttl: int,
        agent: Any = None,
    ) -> Session:
        """重置（清空消息并重建引擎实例）会话。"""
        return self.create(agent_name, ttl, session_id=session_id, agent=agent)

    def drop(self, session_id: str) -> None:
        """删除会话。"""
        self._sessions.pop(session_id, None)

    def clear(self) -> None:
        """清空全部会话。"""
        self._sessions.clear()
