"""
agents.py — エージェント定義

Planner / Executor / Reviewer の3エージェント。
各エージェントは Ollama にメッセージを送り、JSON で結果を返す。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum

import httpx

# ============================================================
# 定数
# ============================================================

OLLAMA_URL = "http://localhost:11434/api/chat"
TIMEOUT    = 180  # seconds


class Model(StrEnum):
    LIGHT = "qwen3:8b"
    CODER = "qwen3-coder:30b"


# ============================================================
# 共有コンテキスト
# ============================================================

@dataclass
class StepRecord:
    step:     str
    action:   str
    output:   str
    score:    int
    passed:   bool


@dataclass
class AgentContext:
    goal:     str
    records:  list[StepRecord] = field(default_factory=list)
    feedback: list[str]        = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"Goal: {self.goal}"]
        for r in self.records[-5:]:
            icon = "✅" if r.passed else "⚠"
            lines.append(f"  {icon} [{r.step[:40]}] → {r.output[:150]}")
        if self.feedback:
            lines.append("Feedback: " + " / ".join(self.feedback[-3:]))
        return "\n".join(lines)


# ============================================================
# Ollama 呼び出し
# ============================================================

def _call(model: str, messages: list[dict]) -> str:
    with httpx.Client(timeout=TIMEOUT) as client:
        resp = client.post(
            OLLAMA_URL,
            json={"model": model, "messages": messages, "stream": False},
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]


def _extract_json(text: str) -> str:
    """<think>タグ除去 + JSONブロック抽出"""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    m = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        return m.group(1)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        return m.group(0)
    return text


def _parse(text: str) -> dict:
    return json.loads(_extract_json(text))


# ============================================================
# 基底クラス
# ============================================================

class BaseAgent:
    SYSTEM: str = ""
    MODEL:  str = Model.CODER

    def _chat(self, user: str) -> dict:
        messages = [
            {"role": "system", "content": self.SYSTEM},
            {"role": "user",   "content": user},
        ]
        raw = _call(self.MODEL, messages)
        return _parse(raw)


# ============================================================
# PlannerAgent
# ============================================================

class PlannerAgent(BaseAgent):
    MODEL  = Model.CODER
    SYSTEM = """あなたはタスク計画エージェントです。
与えられた指示を達成するための具体的なステップリストを作成してください。
各ステップは単一の具体的なアクションにしてください。
必ず以下のJSON形式のみで返してください（説明文不要）:
{"steps": ["ステップ1", "ステップ2", ...]}"""

    def plan(self, instruction: str, context: AgentContext) -> list[str]:
        user = f"指示: {instruction}"
        if context.records or context.feedback:
            user += f"\n\n現在の状況:\n{context.summary()}"
        result = self._chat(user)
        return result["steps"]


# ============================================================
# ExecutorAgent
# ============================================================

class ExecutorAgent(BaseAgent):
    MODEL  = Model.CODER
    SYSTEM = """あなたはコマンド実行エージェントです。
与えられたステップを実行するための具体的なコードを生成してください。
必ず以下のJSON形式のみで返してください（説明文不要）:
{"type": "shell" または "python", "code": "実行するコード"}
- shell: bash コマンド1行または複数行
- python: Pythonコード
安全で副作用の少ないコードのみ生成してください。"""

    def generate(self, step: str, context: AgentContext, feedback: str = "") -> dict:
        user = f"ステップ: {step}\n\n文脈:\n{context.summary()}"
        if feedback:
            user += f"\n\n前回の失敗フィードバック: {feedback}"
        return self._chat(user)


# ============================================================
# ReviewerAgent
# ============================================================

class ReviewerAgent(BaseAgent):
    MODEL  = Model.LIGHT
    SYSTEM = """あなたはレビューエージェントです。
実行結果がステップの目標を達成しているか評価してください。
必ず以下のJSON形式のみで返してください（説明文不要）:
{"passed": true または false, "score": 0〜100, "feedback": "評価コメント"}"""

    def review_step(self, step: str, output: str, goal: str) -> dict:
        user = (
            f"全体目標: {goal}\n"
            f"ステップ: {step}\n"
            f"実行結果:\n{output[:1000]}"
        )
        return self._chat(user)

    def review_all(self, context: AgentContext) -> dict:
        summary = "\n".join(
            f"  {'✅' if r.passed else '⚠'} [{r.step}] score={r.score}: {r.output[:200]}"
            for r in context.records
        )
        user = f"全体目標: {context.goal}\n\n全ステップの結果:\n{summary}"
        return self._chat(user)
