from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(slots=True)
class Settings:
    """Runtime settings loaded from environment variables."""

    host: str = "0.0.0.0"
    port: int = 8000
    database_path: str = "data/focuscube.db"
    timezone: str = "Asia/Shanghai"
    cors_origins: str = "*"

    # 默认查询逻辑基座融合视图。两个物理节点分别保留自身身份：
    # EYE 上传姿态、会话和派生结果，C3 上传 AS7341 原始光照。
    active_device_id: str = "focuscube-base-01"
    default_installation_id: str = "focuscube-base-01"

    # 小组正式使用：火山引擎边缘大模型网关（AI Gateway）。
    # base_url、api_key、model 必须复制控制台“查看代码”中的实际值。
    llm_provider: str = "volcengine_ai_gateway"
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    llm_timeout_s: float = 30.0

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()

        return cls(
            host=os.getenv(
                "FOCUSCUBE_HOST",
                "0.0.0.0",
            ),
            port=int(
                os.getenv(
                    "FOCUSCUBE_PORT",
                    "8000",
                )
            ),
            database_path=os.getenv(
                "FOCUSCUBE_DATABASE_PATH",
                "data/focuscube.db",
            ),
            timezone=os.getenv(
                "FOCUSCUBE_TIMEZONE",
                "Asia/Shanghai",
            ),
            cors_origins=os.getenv(
                "FOCUSCUBE_CORS_ORIGINS",
                "*",
            ),
            active_device_id=os.getenv(
                "FOCUSCUBE_ACTIVE_DEVICE_ID",
                "focuscube-base-01",
            ),
            default_installation_id=os.getenv(
                "FOCUSCUBE_DEFAULT_INSTALLATION_ID",
                "focuscube-base-01",
            ),
            llm_provider=os.getenv(
                "FOCUSCUBE_LLM_PROVIDER",
                "volcengine_ai_gateway",
            ),
            llm_api_key=os.getenv(
                "FOCUSCUBE_LLM_API_KEY",
                "",
            ),
            llm_base_url=os.getenv(
                "FOCUSCUBE_LLM_BASE_URL",
                "",
            ),
            llm_model=os.getenv(
                "FOCUSCUBE_LLM_MODEL",
                "",
            ),
            llm_timeout_s=float(
                os.getenv(
                    "FOCUSCUBE_LLM_TIMEOUT_S",
                    "30",
                )
            ),
        )

    def resolved_database_path(
        self,
        project_root: Path,
    ) -> Path:
        path = Path(self.database_path)

        if not path.is_absolute():
            path = project_root / path

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        return path

    def parsed_cors_origins(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]

        return [
            item.strip()
            for item in self.cors_origins.split(",")
            if item.strip()
        ]
