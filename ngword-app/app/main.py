"""禁止ワード/機密情報 入力制限「AI アプリ」マイクロサービス（管理者限定）。

本サービス仕様書 8-(8)「禁止ワードや機密情報の入力制限機能を有し、管理者は自由に
設定できること」に対応。源内(genai-web)無改修の管理者限定 exApp。

- ルールは `NGWORD_DB_PATH`(既定 /data/ngwords.db, backend_data 共有) に保存。
- 本サービスが**唯一のライター**。backend は同ファイルを読み取り専用で参照し、
  推論前段（/predict 系・AIアプリ）で入力を検査してブロックする。
- exApp 同期プロトコル:
    リクエスト: { "inputs": { "operation": "view|set", "rules_json": "..." } }
    レスポンス: { "outputs": "<Markdown>" }
- 管理者判定: backend が付与する `x-user-groups` に SystemAdminGroup が必要。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from .ngrules import parse_and_validate, render_rules

API_KEY = os.environ.get("RAG_API_KEY", "local-rag-key")
ADMIN_GROUP = os.environ.get("AUDIT_ADMIN_GROUP", "SystemAdminGroup")
NGWORD_DB_PATH = os.environ.get("NGWORD_DB_PATH", "/data/ngwords.db")

app = FastAPI(title="Open GENAI NG-Word App", version="0.1.0")

_DEFAULT: dict[str, Any] = {
    "enabled": False,
    "case_sensitive": False,
    "words": [],
    "patterns": [],
}


def _check_key(x_api_key: str | None) -> JSONResponse | None:
    if API_KEY and x_api_key != API_KEY:
        return JSONResponse(status_code=401, content={"error": "invalid api key"})
    return None


def _is_admin(x_user_groups: str | None) -> bool:
    groups = [g.strip() for g in (x_user_groups or "").split(",") if g.strip()]
    return ADMIN_GROUP in groups


def _connect():
    import sqlite3

    os.makedirs(os.path.dirname(NGWORD_DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(NGWORD_DB_PATH, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _init_db() -> None:
    with _connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS ngword_rules ("
            " id INTEGER PRIMARY KEY CHECK (id = 1),"
            " rules TEXT NOT NULL,"
            " updatedDate TEXT NOT NULL)"
        )


def _read_rules() -> dict[str, Any]:
    try:
        with _connect() as conn:
            row = conn.execute("SELECT rules FROM ngword_rules WHERE id = 1").fetchone()
        if row and row[0]:
            data = json.loads(row[0])
            if isinstance(data, dict):
                return data
    except Exception:  # noqa: BLE001
        pass
    return dict(_DEFAULT)


def _write_rules(rules: dict[str, Any]) -> None:
    now = str(int(time.time() * 1000))
    with _connect() as conn:
        conn.execute(
            "INSERT INTO ngword_rules (id, rules, updatedDate) VALUES (1, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET rules = excluded.rules,"
            " updatedDate = excluded.updatedDate",
            (json.dumps(rules, ensure_ascii=False), now),
        )


@app.on_event("startup")
def _startup() -> None:
    try:
        _init_db()
    except Exception as e:  # noqa: BLE001
        print(f"[ngword] init 失敗: {e}")


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "db": NGWORD_DB_PATH}


_EXAMPLE = (
    '{\n'
    '  "enabled": true,\n'
    '  "case_sensitive": false,\n'
    '  "words": ["禁止語の例"],\n'
    '  "patterns": ["\\\\d{12}"]\n'
    '}'
)


@app.post("/invoke")
async def invoke(
    request: Request,
    x_api_key: str | None = Header(default=None),
    x_user_groups: str | None = Header(default=None),
) -> Any:
    err = _check_key(x_api_key)
    if err:
        return err
    if not _is_admin(x_user_groups):
        return {
            "outputs": (
                "この機能は**システム管理者のみ**が利用できます"
                "（SystemAdminGroup 所属が必要です）。"
            )
        }

    body = await request.json()
    inputs = body.get("inputs", body)
    operation = (inputs.get("operation") or "view").strip().lower()

    if operation == "set":
        rules, verr = parse_and_validate(inputs.get("rules_json") or "")
        if verr:
            return {"outputs": f"設定エラー: {verr}\n\n記入例:\n```json\n{_EXAMPLE}\n```"}
        try:
            _write_rules(rules)
        except Exception as e:  # noqa: BLE001
            return {"outputs": f"[ルールの保存に失敗しました] {e}"}
        return {"outputs": "入力制限ルールを更新しました。\n\n" + render_rules(rules)}

    rules = _read_rules()
    return {
        "outputs": (
            render_rules(rules)
            + "\n\n---\n設定するには「操作」で **設定** を選び、`ルールJSON` に記入例の形式で入力してください:\n"
            + f"```json\n{_EXAMPLE}\n```"
        )
    }
