"""向后兼容入口，推荐使用 run_eval.py。"""
from eval_engine import LLMEvaluationRunner, main, parse_args

if __name__ == "__main__":
    raise SystemExit(main())
