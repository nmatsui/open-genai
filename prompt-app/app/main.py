"""プロンプトテンプレート「AI アプリ」マイクロサービス。

本サービス仕様書 6-(20)(21)(22):
- (20) プロンプトテンプレート機能
- (21) 標準で利用可能なテンプレートが存在
- (22) 利用者が作成し、組織/グループで共有できる

源内(genai-web)無改修。テンプレートを選ぶと、本文（変数置換後）を **チャットへ
流し込むディープリンク**（`/chat?content=...`）を返す。全ユーザーが利用可能。

exApp 同期プロトコル:
    リクエスト: { "inputs": { "operation": "use|list|create|delete", ... } }
    レスポンス: { "outputs": "<Markdown>" }
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from . import catalog

API_KEY = os.environ.get("RAG_API_KEY", "local-rag-key")
ADMIN_GROUP = os.environ.get("AUDIT_ADMIN_GROUP", "SystemAdminGroup")

app = FastAPI(title="Open GENAI Prompt Template App", version="0.1.0")

# 標準テンプレート（6-(21)）。汎用の行政実務向け（特定自治体に依存しない）。
STANDARD_TEMPLATES = [
    {
        "id": "std-minutes",
        "title": "議事録の要約",
        "target": "content",
        "body": (
            "以下の会議メモを、次の観点で日本語で整理・要約してください。\n"
            "- 決定事項\n- ToDo（担当・期限つき）\n- 主要な論点\n\n"
            "【会議メモ】\n{{メモ}}"
        ),
    },
    {
        "id": "std-proofread",
        "title": "文章の校正",
        "target": "content",
        "body": "次の文章を、意味を変えずに誤字脱字・表現を整えて校正してください。\n\n{{本文}}",
    },
    {
        "id": "std-mail",
        "title": "ビジネスメールの作成",
        "target": "content",
        "body": (
            "次の要点で、丁寧なビジネスメールの文面を作成してください。\n\n"
            "宛先: {{宛先}}\n件名の方向性: {{件名}}\n伝えたい要点: {{要点}}"
        ),
    },
    {
        "id": "std-explain",
        "title": "わかりやすい説明",
        "target": "content",
        "body": "次の内容を、専門知識のない人にも分かるように、平易な言葉で説明してください。\n\n{{内容}}",
    },
    {
        "id": "std-summarize",
        "title": "長文の要約",
        "target": "content",
        "body": "次の文章を、重要点を落とさずに3〜5個の箇条書きで要約してください。\n\n{{本文}}",
    },
]


def _check_key(x_api_key: str | None) -> JSONResponse | None:
    if API_KEY and x_api_key != API_KEY:
        return JSONResponse(status_code=401, content={"error": "invalid api key"})
    return None


def _groups(x_user_groups: str | None) -> list[str]:
    return [g.strip() for g in (x_user_groups or "").split(",") if g.strip()]


def _is_admin(x_user_groups: str | None) -> bool:
    return ADMIN_GROUP in set(_groups(x_user_groups))


@app.on_event("startup")
def _startup() -> None:
    try:
        catalog.init_db()
        # 標準テンプレートが未登録なら投入（冪等）
        for t in STANDARD_TEMPLATES:
            if catalog.get_template(t["id"]) is None:
                catalog.create_template(
                    title=t["title"],
                    body=t["body"],
                    owner_user="system",
                    target=t.get("target", "content"),
                    is_standard=True,
                    template_id=t["id"],
                )
    except Exception as e:  # noqa: BLE001
        print(f"[prompt-app] 初期化に失敗: {e}")


@app.get("/health")
async def health() -> dict[str, Any]:
    try:
        n = catalog.count()
    except Exception:  # noqa: BLE001
        n = -1
    return {"status": "ok", "templates": n}


def _render_list(items: list[dict[str, Any]]) -> str:
    if not items:
        return "利用可能なテンプレートはありません。"
    lines = ["## 利用可能なテンプレート", "", "| ID | タイトル | 区分 |", "| --- | --- | --- |"]
    for t in items:
        kind = "標準" if t["isStandard"] else ("共有" if t["sharedGroups"] else "個人")
        lines.append(f"| `{t['id']}` | {t['title']} | {kind} |")
    lines.append("")
    lines.append("> 「使う」で ID を指定すると、チャットへ流し込むリンクを表示します。")
    return "\n".join(lines)


@app.post("/invoke")
async def invoke(
    request: Request,
    x_api_key: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_user_groups: str | None = Header(default=None),
) -> Any:
    err = _check_key(x_api_key)
    if err:
        return err

    user_id = (x_user_id or "").strip()
    groups = _groups(x_user_groups)
    is_admin = _is_admin(x_user_groups)

    body = await request.json()
    inputs = body.get("inputs", body)
    operation = (inputs.get("operation") or "use").strip().lower()

    if operation == "list":
        return {"outputs": _render_list(catalog.list_visible(user_id, groups, is_admin))}

    if operation == "create":
        title = (inputs.get("title") or "").strip()
        tbody = (inputs.get("body") or "").strip()
        if not title or not tbody:
            return {"outputs": "タイトルと本文を入力してください。"}
        share = (inputs.get("share") or "personal").strip().lower()
        target = (inputs.get("target") or "content").strip().lower()
        is_standard = False
        shared_groups: list[str] = []
        if share == "standard":
            if not is_admin:
                return {"outputs": "標準テンプレートの作成はシステム管理者のみ可能です。"}
            is_standard = True
        elif share == "group":
            grp = (inputs.get("share_group") or "").strip()
            if not grp:
                return {"outputs": "共有先グループ名（share_group）を指定してください。"}
            shared_groups = [grp]
        tid = catalog.create_template(
            title=title,
            body=tbody,
            owner_user=user_id,
            target=target,
            shared_groups=shared_groups,
            is_standard=is_standard,
        )
        return {"outputs": f"テンプレートを作成しました（ID: `{tid}`）。「一覧」で確認できます。"}

    if operation == "delete":
        tid = (inputs.get("template_id") or "").strip()
        t = catalog.get_template(tid) if tid else None
        if not t:
            return {"outputs": "指定 ID のテンプレートが見つかりません。"}
        if not catalog.can_delete(t, user_id, is_admin):
            return {"outputs": "このテンプレートを削除する権限がありません。"}
        catalog.delete_template(tid)
        return {"outputs": f"テンプレート `{tid}` を削除しました。"}

    # use（既定）
    tid = (inputs.get("template_id") or "").strip()
    if not tid:
        listing = _render_list(catalog.list_visible(user_id, groups, is_admin))
        return {"outputs": "使用するテンプレートの ID を指定してください。\n\n" + listing}
    t = catalog.get_template(tid)
    if not t:
        return {"outputs": "指定 ID のテンプレートが見つかりません。"}
    # 可視性チェック（他人の個人テンプレは使えない）
    visible_ids = {x["id"] for x in catalog.list_visible(user_id, groups, is_admin)}
    if tid not in visible_ids:
        return {"outputs": "このテンプレートを利用する権限がありません。"}

    variables = catalog.parse_vars(inputs.get("variables"))
    filled, missing = catalog.substitute(t["body"], variables)
    link = catalog.build_deeplink(filled, target=t.get("target", "content"), auto_submit=False)

    out = [f"## {t['title']}", "", "```text", filled, "```", ""]
    if missing:
        out.append(f"> 未入力の変数: {', '.join('{{' + m + '}}' for m in missing)}"
                   "（「変数」に `キー: 値` の形式で指定できます）")
        out.append("")
    out.append(f"👉 [このプロンプトでチャットを開く]({link})")
    return {"outputs": "\n".join(out)}
