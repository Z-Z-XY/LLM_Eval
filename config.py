"""评测框架统一配置：环境变量 + CLI 覆盖，供 run_eval / 库调用复用。"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import argparse


def env_str(key: str, default: str = "") -> str:
    val = os.getenv(key, default)
    if not val:
        return default
    return val.strip().strip('"').strip("'")


def env_bool(key: str, default: bool = False) -> bool:
    val = env_str(key, "true" if default else "false").lower()
    return val in ("1", "true", "yes")


def env_float(key: str, default: float) -> float:
    raw = env_str(key, str(default))
    try:
        return float(raw)
    except ValueError:
        return default


def env_int(key: str, default: int) -> int:
    raw = env_str(key, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


def env_float_optional(key: str) -> float | None:
    raw = env_str(key)
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


# CI 门禁默认及格线（%）；可通过 EVAL_FAIL_UNDER 或 --fail-under 覆盖
DEFAULT_CI_FAIL_UNDER = 85.0

# 评测集用途分层：golden 基准 / regression 历史故障 / adversarial 红队 / smoke 快检
VALID_EVAL_SUITES = frozenset({"golden", "regression", "adversarial", "smoke"})
DEFAULT_CI_SUITE = "golden"


@dataclass(frozen=True)
class EvalConfig:
    """一次评测运行的完整配置快照。"""

    api_key: str
    base_url: str
    model: str
    judge_model: str
    judge_model_from_env: bool
    timeout: float
    max_tokens: int
    case_interval_sec: float
    api_max_retries: int
    eval_limit: int
    eval_engine: str
    eval_strict: bool
    metric_threshold: float
    use_system_proxy: bool
    langfuse_host: str
    langfuse_disabled: bool
    langfuse_public_key: str
    langfuse_secret_key: str
    dataset_path: Path
    output_dir: Path
    output_json_path: Path | None = None
    fail_under: float | None = None
    eval_suite: str | None = None
    ci_mode: bool = False
    dry_run: bool = False
    verbose: bool = False

    @classmethod
    def from_env(
        cls,
        *,
        script_dir: Path,
        project_dir: Path | None = None,
    ) -> EvalConfig:
        project_dir = project_dir or script_dir.parent
        base = (env_str("SILICONFLOW_BASE_URL") or "https://api.siliconflow.cn/v1").rstrip("/")
        if not base.endswith("/v1"):
            base = f"{base}/v1"
        model = env_str("SILICONFLOW_MODEL", "deepseek-ai/DeepSeek-V4")
        judge_env = env_str("SILICONFLOW_JUDGE_MODEL")
        strict = env_bool("EVAL_STRICT", True)
        return cls(
            api_key=env_str("SILICONFLOW_API_KEY"),
            base_url=base,
            model=model,
            judge_model=judge_env or model,
            judge_model_from_env=bool(judge_env),
            timeout=env_float("SILICONFLOW_TIMEOUT", 120.0),
            max_tokens=env_int("SILICONFLOW_MAX_TOKENS", 1024),
            case_interval_sec=env_float("CASE_INTERVAL_SEC", 2.0),
            api_max_retries=env_int("API_MAX_RETRIES", 3),
            eval_limit=env_int("EVAL_LIMIT", 0),
            eval_engine=env_str("EVAL_ENGINE", "deepeval").lower(),
            eval_strict=strict,
            metric_threshold=env_float("EVAL_METRIC_THRESHOLD", 0.7 if strict else 0.5),
            use_system_proxy=env_bool("SILICONFLOW_USE_SYSTEM_PROXY"),
            langfuse_host=env_str("LANGFUSE_HOST") or env_str("LANGFUSE_BASE_URL") or "https://cloud.langfuse.com",
            langfuse_disabled=env_bool("LANGFUSE_DISABLED"),
            langfuse_public_key=env_str("LANGFUSE_PUBLIC_KEY"),
            langfuse_secret_key=env_str("LANGFUSE_SECRET_KEY"),
            dataset_path=script_dir / "eval_dataset.json",
            output_dir=script_dir,
            fail_under=env_float_optional("EVAL_FAIL_UNDER"),
            eval_suite=(env_str("EVAL_SUITE").lower() or None) if env_str("EVAL_SUITE") else None,
            ci_mode=env_bool("EVAL_CI"),
        )

    def with_cli(self, args: argparse.Namespace | None) -> EvalConfig:
        if args is None:
            return self
        updates: dict[str, Any] = {}
        if getattr(args, "model", None):
            updates["model"] = args.model
            if not self.judge_model_from_env and not getattr(args, "judge_model", None):
                updates["judge_model"] = args.model
        if getattr(args, "judge_model", None):
            updates["judge_model"] = args.judge_model
            updates["judge_model_from_env"] = True
        if getattr(args, "dataset", None):
            updates["dataset_path"] = Path(args.dataset).resolve()
        if getattr(args, "output", None):
            out = Path(args.output).resolve()
            if out.suffix.lower() == ".json":
                updates["output_dir"] = out.parent
                updates["output_json_path"] = out
            else:
                updates["output_dir"] = out
                updates["output_json_path"] = None
        if getattr(args, "engine", None):
            updates["eval_engine"] = args.engine.lower()
        if getattr(args, "limit", None) is not None:
            updates["eval_limit"] = args.limit
        if getattr(args, "strict", None) is not None:
            updates["eval_strict"] = args.strict
        if getattr(args, "no_langfuse", False):
            updates["langfuse_disabled"] = True
        if getattr(args, "fail_under", None) is not None:
            updates["fail_under"] = args.fail_under
        if getattr(args, "suite", None):
            updates["eval_suite"] = args.suite.lower()
        if getattr(args, "ci", False):
            updates["ci_mode"] = True
        if getattr(args, "dry_run", False):
            updates["dry_run"] = True
        if getattr(args, "verbose", False):
            updates["verbose"] = True
        return replace(self, **updates) if updates else self

    def finalize(self) -> EvalConfig:
        """应用 CI 等运行预设（在环境变量与 CLI 合并之后调用）。"""
        if not self.ci_mode:
            return self
        updates: dict[str, Any] = {}
        if not self.langfuse_disabled:
            updates["langfuse_disabled"] = True
        if self.fail_under is None:
            updates["fail_under"] = DEFAULT_CI_FAIL_UNDER
        if self.eval_suite is None:
            updates["eval_suite"] = DEFAULT_CI_SUITE
        return replace(self, **updates) if updates else self

    def validate(self) -> None:
        if self.eval_engine not in ("simple", "deepeval"):
            raise ValueError("eval_engine 仅支持 simple 或 deepeval")
        if self.eval_suite is not None and self.eval_suite not in VALID_EVAL_SUITES:
            allowed = ", ".join(sorted(VALID_EVAL_SUITES))
            raise ValueError(f"eval_suite 无效: {self.eval_suite!r}，可选: {allowed}")
        if not self.dry_run and not self.api_key:
            raise RuntimeError("缺少 SILICONFLOW_API_KEY，请在 .env 中配置或使用 --dry-run 校验数据集。")

    def apply_runtime_globals(self, module: Any) -> None:
        """将配置同步到评测主模块的全局变量（兼容现有函数式实现）。"""
        module.SILICONFLOW_MODEL = self.model
        module.SILICONFLOW_JUDGE_MODEL = self.judge_model
        module.SILICONFLOW_TIMEOUT = self.timeout
        module.SILICONFLOW_MAX_TOKENS = self.max_tokens
        module.CASE_INTERVAL_SEC = self.case_interval_sec
        module.API_MAX_RETRIES = self.api_max_retries
        module.EVAL_LIMIT = self.eval_limit
        module.EVAL_ENGINE = self.eval_engine
        module.EVAL_STRICT = self.eval_strict
        module.LANGFUSE_DISABLED = self.langfuse_disabled
        module.LANGFUSE_HOST = self.langfuse_host
        module._deepeval_runner = None

        try:
            import config as cfg_mod
            cfg_mod.set_active(self)
        except ImportError:
            pass

        try:
            import deepeval_metrics as dm
            dm.set_runtime_config(self.eval_strict, self.metric_threshold)
        except ImportError:
            pass


_ACTIVE: EvalConfig | None = None


def set_active(config: EvalConfig) -> None:
    global _ACTIVE
    _ACTIVE = config


def get_active_config() -> EvalConfig | None:
    return _ACTIVE
