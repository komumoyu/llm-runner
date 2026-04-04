# llm-runner

LLM エージェント実行ツール。複数エージェントの構成と安全機能を備えた Python 製ランナー。

## 構成

| ファイル | 役割 |
|---|---|
| `runner.py` | メインランナー — LLM の実行制御 |
| `agents.py` | エージェント定義・構成管理 |
| `safety.py` | 安全機能（出力フィルタリング等） |

## セットアップ

```bash
pip install -r requirements.txt
python runner.py
```

