"""Start the local real-AI Demo against an existing, persistent dataset."""

from __future__ import annotations

import argparse
import math
import os
import sqlite3
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


class LauncherError(Exception):
    pass


@dataclass(frozen=True)
class LaunchConfig:
    environment: dict[str, str] = field(repr=False)
    cwd: Path
    port: int


def prepare_launch(
    data_dir: Path,
    *,
    live: bool = False,
    max_cost_cny: float | None = None,
    port: int = 8765,
    repo_root: Path = REPO_ROOT,
    environ: Mapping[str, str] | None = None,
) -> LaunchConfig:
    """Validate without creating, migrating, copying or resetting any database."""
    if not 1 <= port <= 65535:
        raise LauncherError("端口必须在 1–65535 之间。")
    if live and max_cost_cny is None:
        raise LauncherError("真实问答需要 --max-cost-cny 指定已经授权的累计估算额度。")
    if max_cost_cny is not None and (
        not math.isfinite(max_cost_cny) or not 0.25 <= max_cost_cny <= 10
    ):
        raise LauncherError("累计估算额度必须在 0.25–10 元之间。")

    repo_root = repo_root.resolve()
    data_dir = data_dir.resolve()
    database = data_dir / "processed" / "bidops.sqlite3"
    if not database.is_file():
        raise LauncherError("未找到已有 Demo 数据库；请先完成导入，启动器不会创建空库。")
    if not (repo_root / "frontend" / "dist" / "index.html").is_file():
        raise LauncherError("缺少页面构建，请先运行 npm run build --prefix frontend。")
    if not (repo_root / "config" / "sources.json").is_file():
        raise LauncherError("缺少公告来源配置。")
    raw_dir = data_dir / "raw"
    if not raw_dir.is_dir() or not any(path.is_file() for path in raw_dir.rglob("*")):
        raise LauncherError("缺少公告原始文件，请使用与数据库配套的完整 Demo 数据目录。")
    try:
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
        try:
            notice_count = connection.execute("SELECT count(*) FROM notices").fetchone()[0]
            connection.execute("SELECT count(*) FROM agent_runs").fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        raise LauncherError("数据库不可读或缺少公告/费用表；请先完成数据准备。") from None
    if not notice_count:
        raise LauncherError("数据库中没有已解析公告，请先完成导入和解析。")

    original = dict(os.environ if environ is None else environ)
    environment = {
        key: value
        for key, value in original.items()
        if not key.startswith(("BIDOPS_", "DEEPSEEK_", "UVICORN_")) and key != "WEB_CONCURRENCY"
    }
    if live:
        key = original.get("DEEPSEEK_API_KEY", "").strip()
        if not key:
            from dotenv import dotenv_values

            key = (
                dotenv_values(repo_root / "private" / "deepseek.env", interpolate=False).get(
                    "DEEPSEEK_API_KEY"
                )
                or ""
            ).strip()
        if not key:
            raise LauncherError("缺少 DeepSeek 密钥，请在本机环境或 private/deepseek.env 配置。")
        environment["DEEPSEEK_API_KEY"] = key
    environment.update(
        {
            "BIDOPS_DATA_DIR": str(data_dir),
            "BIDOPS_DATABASE_URL": f"sqlite:///{database}",
            "BIDOPS_SOURCE_REGISTRY": str(repo_root / "config" / "sources.json"),
            "BIDOPS_NETWORK_ENABLED": "false",
            "BIDOPS_ENTERPRISE_ENABLED": "false",
            "BIDOPS_SCREENING_ENABLED": "false",
            "BIDOPS_SCREENING_LIVE_CALLS_ENABLED": "false",
            "BIDOPS_JOBS_ENABLED": "false",
            "BIDOPS_REVIEW_ENABLED": "false",
            "BIDOPS_INGESTION_ENABLED": "false",
            "BIDOPS_OPERATIONS_ENABLED": "false",
            "BIDOPS_DEMO_ENABLED": "true" if live else "false",
            "BIDOPS_DEMO_MAX_COST_CNY": str(max_cost_cny if max_cost_cny is not None else 1),
            "BIDOPS_DEMO_MODEL_NAME": "deepseek-flash",
            "BIDOPS_DEMO_MAX_RUN_SECONDS": "60",
            "BIDOPS_DEMO_MAX_SESSIONS": "50",
            "BIDOPS_DEMO_MAX_TURNS": "8",
            "BIDOPS_DEMO_BUDGET_RESTORE_TOKEN": "",
            "PYDANTIC_AI_NO_BANNER": "1",
        }
    )
    return LaunchConfig(environment=environment, cwd=repo_root, port=port)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="使用已有数据启动本机 BidOps Demo")
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--live", action="store_true", help="启用已授权的真实 DeepSeek 问答")
    parser.add_argument("--max-cost-cny", type=float, help="本数据目录账本累计估算额度，重启不重置")
    args = parser.parse_args(argv)
    try:
        config = prepare_launch(
            args.data_dir, live=args.live, max_cost_cny=args.max_cost_cny, port=args.port
        )
    except LauncherError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"BidOps Demo：http://127.0.0.1:{config.port}", flush=True)
    print("模式：真实 AI 问答" if args.live else "模式：页面预览（模型调用关闭）", flush=True)
    print("复用已有公告与费用记录；服务启动会执行原有数据库版本检查。", flush=True)
    os.chdir(config.cwd)
    os.execve(
        sys.executable,
        [
            sys.executable,
            "-m",
            "uvicorn",
            "bidops.api:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(config.port),
            "--workers",
            "1",
        ],
        config.environment,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
