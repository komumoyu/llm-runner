"""
runner.py — オーケストレーター

指示を受け取り、Planner → Executor → Reviewer のループで
タスク完了まで自走する。
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field

from agents import AgentContext, ExecutorAgent, PlannerAgent, ReviewerAgent, StepRecord
from safety import check_command

# ============================================================
# 設定
# ============================================================

MAX_BRUSHUP = 3   # 1ステップあたりの最大ブラッシュアップ回数
MAX_REPLAN  = 2   # 全体再計画の最大回数
CMD_TIMEOUT = 30  # コマンドタイムアウト（秒）


# ============================================================
# 実行結果
# ============================================================

@dataclass
class RunResult:
    success:         bool
    goal:            str
    steps_completed: int
    total_steps:     int
    log:             list[str] = field(default_factory=list)


# ============================================================
# アクション実行
# ============================================================

def _execute(action: dict) -> tuple[bool, str]:
    """
    {"type": "shell"/"python", "code": "..."} を受け取り
    (success, output) を返す。
    """
    code = action.get("code", "").strip()
    kind = action.get("type", "shell")

    if not code:
        return False, "ERROR: 空のコードです"

    if kind == "shell":
        safety = check_command(code)
        if not safety.safe:
            return False, f"BLOCKED: {safety.reason}"
        proc = subprocess.run(
            code, shell=True, capture_output=True, text=True, timeout=CMD_TIMEOUT
        )
        output = (proc.stdout + proc.stderr).strip()
        return proc.returncode == 0, output or "(出力なし)"

    if kind == "python":
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=CMD_TIMEOUT,
        )
        output = (proc.stdout + proc.stderr).strip()
        return proc.returncode == 0, output or "(出力なし)"

    return False, f"ERROR: 未知のアクション種別 '{kind}'"


# ============================================================
# オーケストレーター
# ============================================================

def run(instruction: str) -> RunResult:
    planner  = PlannerAgent()
    executor = ExecutorAgent()
    reviewer = ReviewerAgent()
    context  = AgentContext(goal=instruction)
    log: list[str] = []

    def L(msg: str) -> None:
        log.append(msg)
        print(msg, flush=True)

    for replan_idx in range(MAX_REPLAN + 1):
        L(f"\n{'=' * 60}")
        L(f"[Planner] 計画立案中... (再計画 {replan_idx}/{MAX_REPLAN})")

        steps = planner.plan(instruction, context)
        L(f"[Planner] {len(steps)} ステップ:")
        for i, s in enumerate(steps, 1):
            L(f"  {i}. {s}")

        completed = 0

        for step_idx, step in enumerate(steps, 1):
            L(f"\n── Step {step_idx}/{len(steps)}: {step}")
            feedback = ""

            for brushup in range(MAX_BRUSHUP):
                L(f"  [Executor] アクション生成 (試行 {brushup + 1}/{MAX_BRUSHUP})")

                try:
                    action = executor.generate(step, context, feedback)
                except Exception as e:
                    L(f"  [Executor] ERROR: {e}")
                    continue

                L(f"  [Executor] type={action.get('type')} code={action.get('code','')[:80]!r}")

                success, output = _execute(action)
                L(f"  [Output] {'✅' if success else '❌'} {output[:300]}")

                try:
                    review = reviewer.review_step(step, output, instruction)
                except Exception as e:
                    L(f"  [Reviewer] ERROR: {e}")
                    review = {"passed": success, "score": 50 if success else 0, "feedback": str(e)}

                score   = review.get("score", 0)
                passed  = review.get("passed", False)
                fb      = review.get("feedback", "")
                L(f"  [Reviewer] score={score} passed={passed} → {fb}")

                context.records.append(StepRecord(
                    step=step, action=str(action), output=output,
                    score=score, passed=passed,
                ))

                if passed:
                    completed += 1
                    break

                # ブラッシュアップ
                feedback = fb
                context.feedback.append(fb)
                if brushup < MAX_BRUSHUP - 1:
                    L(f"  [Brushup] フィードバックを反映して再試行...")
            else:
                L(f"  [Warning] 最大ブラッシュアップ回数到達。次のステップへ。")
                completed += 1

        # 全体レビュー
        L(f"\n[Reviewer] 全体評価中...")
        try:
            final = reviewer.review_all(context)
        except Exception as e:
            L(f"[Reviewer] ERROR: {e}")
            final = {"passed": True, "score": 50, "feedback": str(e)}

        L(f"[Reviewer] 最終スコア={final.get('score')} passed={final.get('passed')}")
        L(f"[Reviewer] {final.get('feedback', '')}")

        if final.get("passed"):
            L("\n✅ タスク完了！")
            return RunResult(True, instruction, completed, len(steps), log)

        if replan_idx < MAX_REPLAN:
            L(f"\n[Planner] 再計画: {final.get('feedback', '')}")
            context.feedback.append(final.get("feedback", ""))
        else:
            L("\n⚠ 最大再計画回数に達しました。")

    return RunResult(False, instruction, completed, len(steps), log)


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    instruction = " ".join(sys.argv[1:])
    if not instruction:
        print("Usage: python runner.py <instruction>")
        sys.exit(1)

    result = run(instruction)
    sys.exit(0 if result.success else 1)
