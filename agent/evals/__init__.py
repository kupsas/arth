"""
Eval & benchmarking harness for the Arth agent (Sub-Plan 4).

Import symbols from submodules to avoid pulling FastAPI / LiteLLM on ``import agent.evals``::

    from agent.evals.dataset import EvalQuestion, load_eval_questions
    from agent.evals.runner import EvalRunResult, run_eval_suite, write_eval_json
    from agent.evals.scorer import auto_score_question_result

Run::

    python scripts/run_evals.py --help
"""

from agent.evals.dataset import EvalQuestion, load_eval_questions

__all__ = ["EvalQuestion", "load_eval_questions"]
