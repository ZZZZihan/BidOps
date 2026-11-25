"""Launch a local full workflow using the existing real-notice dataset and ledger."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from live_demo import REPO_ROOT, LaunchConfig, LauncherError  # noqa: E402
from live_demo import prepare_launch as prepare_existing  # noqa: E402


def prepare_launch(
    data_dir: Path,
    *,
    live: bool = False,
    max_cost_cny: float | None = None,
    port: int = 8765,
    key_file: Path | None = None,
    repo_root: Path = REPO_ROOT,
    environ: Mapping[str, str] | None = None,
) -> LaunchConfig:
    original = dict(os.environ if environ is None else environ)
    if max_cost_cny is not None and max_cost_cny > 1:
        raise LauncherError("本次 Demo 累计估算额度最多 1 元；不会扩大已授权额度。")
    # An explicit file selects only its key; no other dotenv option is imported.
    if live and key_file is not None:
        from dotenv import dotenv_values

        if not key_file.is_file():
            raise LauncherError("指定的本机密钥文件不存在。")
        key = dotenv_values(key_file, interpolate=False).get("DEEPSEEK_API_KEY") or ""
        if not key.strip():
            raise LauncherError("指定文件未配置 DeepSeek 密钥。")
        original["DEEPSEEK_API_KEY"] = key.strip()
    config = prepare_existing(
        data_dir,
        live=live,
        max_cost_cny=max_cost_cny,
        port=port,
        repo_root=repo_root,
        environ=original,
    )
    config.environment.update(
        {
            "BIDOPS_WORKFLOW_ORIGIN": f"http://127.0.0.1:{port}",
            "BIDOPS_WORKFLOW_LIVE": "true" if live else "false",
            "BIDOPS_WORKFLOW_MAX_COST_CNY": str(max_cost_cny or 1),
        }
    )
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="启动 BidOps 本机完整流程演示")
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--live", action="store_true", help="显式启用已授权的真实 AI")
    parser.add_argument("--max-cost-cny", type=float, help="共用现有账本的累计估算上限，最多1元")
    parser.add_argument("--key-file", type=Path, help="本机密钥文件路径；不会输出内容")
    args = parser.parse_args(argv)
    try:
        config = prepare_launch(
            args.data_dir,
            live=args.live,
            max_cost_cny=args.max_cost_cny,
            port=args.port,
            key_file=args.key_file,
        )
    except LauncherError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"BidOps 本机完整流程：http://127.0.0.1:{config.port}", flush=True)
    print("企业与材料为虚构工程示例；沿用已有公告和全部费用记录。", flush=True)
    print("真实 AI 已启用" if args.live else "页面预览：真实模型调用关闭", flush=True)
    os.chdir(config.cwd)
    os.execve(
        sys.executable,
        [
            sys.executable,
            "-m",
            "uvicorn",
            "bidops.local_demo.app:application",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(config.port),
            "--workers",
            "1",
            "--no-proxy-headers",
        ],
        config.environment,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
