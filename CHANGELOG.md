# Changelog

このファイルは、初期ローカル化リリース（コミット `fc57e53`）以降の変更を記録します。
`fc57e53` 時点では「源内をクラウド依存から切り離し、ローカル LLM で動かす」ことに
焦点を当てていました。その後、**実際の自治体・閉域（LGWAN 等）での運用**を想定し、
思想とアーキテクチャを大きく拡張しています。

## 設計思想の転換（概要）

| 観点 | 当初（`fc57e53` まで） | 現在 |
| --- | --- | --- |
| 目的 | 源内をローカル／OSS スタックで再現する | 自治体の実務・ガバナンス要件を満たしつつ、源内の UX を活かす |
| 改修の置き場 | `backend/` と最小限の `genai-web/` パッチ | **OpenGENAI レイヤ**（`backend/` + 各 exApp + `shared/`）に集約。上流 `genai-web` は極力無改修 |
| 利用者モデル | 源内準拠のチーム管理 | **チーム主体・非階層・複数所属**。1 人が複数チームに所属可能 |
| 管理機能 | Keycloak コンソール等 | 管理者向け **exApp**（監査・利用者一括・モデル制御・入力制限）として源内 UI 内に統合 |
| ファイル出力 | backend ローカル保存 / Dify 直リンク | **自前 S3 互換（SeaweedFS）** へ再ホストし、署名付き URL で受け渡し |
| 公開面 | 各サービスを個別ポート公開 | **nginx リバースプロキシ単一入口**（本番 TLS / 閉域 HTTP 検証） |

---

## [Unreleased]

### 自治体・閉域運用を想定した機能追加

**考え方:** デジタル庁「源内」のクラウド版が暗黙に担っていたガバナンス（監査・権限・データライフサイクル）を、
マネージドサービスに頼らず **OpenGENAI レイヤ**で再実装する。

- **監査ログ**（`backend/app/audit.py`, `audit-app/`）
  - 利用状況・利用内容を append-only で記録。保存期間は最低 3 年を下限に設定
  - チャット履歴とは独立（利用者が履歴を削除しても監査ログは残る）
  - 管理者向け参照 exApp（`audit-app`）を提供
- **チャット履歴の利用者分離**（`backend/app/storage.py`）
  - `chats.userId` による所有者制御。他者の履歴へのアクセスを拒否
- **利用者一括管理**（`usermgmt-app/`）
  - CSV による Keycloak 利用者の一括作成・更新・削除（Admin API 経由）
- **モデル利用制御**（`backend/app/policy.py`, `modelpolicy-app/`）
  - グループ／チーム単位で利用可能な LLM モデルを制限（推論時に強制）
- **入力制限**（`backend/app/ngwords.py`, `ngword-app/`）
  - 禁止語・個人情報パターン（正規表現）を推論前に検査。ブロック時は監査ログに記録
- **プロンプトテンプレート**（`prompt-app/`）
  - 標準／個人／グループ共有テンプレート。`{{変数}}` 置換後、チャットへ流し込むディープリンクを返す
- **契約終了時のデータ完全削除**（`scripts/purge-and-report.sh`）
  - Docker ボリュームの物理削除と削除報告書の生成

### チーム主体・複数所属への拡張

**考え方:** 源内のチーム概念を維持しつつ、自治体の実態（課・プロジェクト・横断チーム）に合わせ、
**親子階層を持たないフラットなチーム**と **1 人複数チーム所属**を第一級に扱う。

- `team_users` の複合主キー `(teamId, userId)` により、同一利用者の複数チーム所属を表現
- AI アプリの可視範囲: **所属チーム + 共通チーム**（システム管理者は全チーム）
- チーム管理者は `team_users.isAdmin` で表現（ログイン時に `TeamAdminGroup` を自動付与）
- 管理者専用ツール用の固定チーム `ADMIN_TEAM_ID` を追加（一般利用者には非表示）
- **保存プロンプト（systemcontexts）の共有**
  - 全体公開（`isPublic`）と、所属チーム ID を **共有タグ**（`sharedTags`）として付与
  - フロント: 保存ダイアログに「全体公開」「チームで共有（複数選択）」を追加
  - API: `GET /me/teams` で所属チーム一覧を取得
- **RAG ナレッジのスコープ**
  - チーム作成時に「検索用」「管理用」の 2 アプリを自動登録（`dynamic_schema: true`）
  - ナレッジは `scope = teamId` で分離。タグによるフラット分類と URL 取り込みに対応

### 源内 UI 制約の拡張（操作性の確保）

**考え方:** 上流 `genai-web` とのマージ容易性を保ち、**後方互換の opt-in 拡張**のみを加える。
管理系の複雑な UI は exApp の動的フォームで賄い、源内本体の改修を最小化する。

- **OpenGENAI exApp Form Spec v1**（`genai-web/.../FORM_SPEC.md`）
  - `visibleWhen`（条件表示）、`reactive`（入力に応じたフォーム再取得）、`preview` 型
  - `/exapps/resolve` プロキシと exApp 側 `/resolve` エンドポイント
- **`dynamic_schema: true`**
  - Dify 以外のローカル exApp（RAG 管理・プロンプトカタログ等）でも実行時フォーム生成
- **各画面の「使い方」開閉 UI**
  - チャット・翻訳・文字起こし・文章生成・ダイアグラム・画像生成に折りたたみヘルプを追加
- **ダイアグラム生成の堅牢化**
  - Mermaid フェンス無し出力・`<description>` タグ混在へのフォールバック抽出
  - 取得失敗時のエラー表示
- **プロンプトテンプレート → チャット連携**
  - 源内既存の `/chat?content=` 取り込みを利用（genai-web 本体はクエリ対応のみで済む）

### 生成ファイルのオブジェクトストレージ配置

**考え方:** 第三者（Dify 等）の署名付き URL を利用者に直接渡さず、**自前ホストの OSS S3 互換**
ストレージへ再ホストしてから署名付き URL で配信する（マネージド S3 非依存）。

- **SeaweedFS**（S3 API）を `docker-compose` に同梱（`seaweedfs_data` ボリューム）
- **`backend/app/objstore.py`**
  - boto3 によるアップロード・プレフィックス削除・署名付き URL 生成
  - 内部アップロード用と公開用エンドポイントの分離（`S3_ENDPOINT_URL` / `S3_PUBLIC_ENDPOINT`）
- **`dify-app`**
  - ファイル出力を構造化 artifact（`file_url`, `display_name`, `mime`, `size`）として返却
  - `backend` の `invoke_exapp` が artifact を SeaweedFS へ再ホストし、ダウンロードリンクを outputs に注入
  - 監査ログに `file.output` を記録
- 未設定時は従来どおりフォールバック（objstore 無効）

### インフラ・セキュリティ

**考え方:** 閉域・本番では **proxy のみを外部公開**し、内部サービス間の信頼境界を明示する。

- **nginx リバースプロキシ単一入口**
  - 開発: `docker-compose.yml` + `proxy/nginx.http.conf`（HTTP :80）
  - 本番: `docker-compose.prod.yml` + `proxy/nginx.conf`（TLS :443 終端）
  - 閉域検証: `docker-compose.verify.yml`（HTTP のみ・自己署名証明書不要）
  - ルーティング: `/` → web、`/api` → backend、`/kc` → Keycloak
- **内部サービス間 HMAC 署名**（各 `app/intauth.py`）
  - backend → exApp の `x-user-*` ヘッダ偽装対策（`INTERNAL_SIGNING_SECRET`）
- **SSRF 対策**（`shared/ssrfguard.py`）
  - 成果物 URL 再取得・RAG の URL 取り込みで DNS リバインディング対策付き HTTP 取得
  - 許可ホストリスト（`ARTIFACT_FETCH_ALLOWED_HOSTS`, `URL_FETCH_ALLOWED_HOSTS`）
- **RAG の URL 取り込み**（`rag-app/app/urlfetch.py`, `urlstore.py`）
  - 行政 HP 等の URL を定期再クロール（`URL_REFRESH_INTERVAL`）
  - フォルダ階層 ACL は廃止し、**タグ + URL + スコープ（teamId）** のフラットモデルに整理

### 構成・ドキュメント

- 新規サービス: `audit-app/`, `modelpolicy-app/`, `ngword-app/`, `prompt-app/`, `usermgmt-app/`
- 新規共有: `shared/ssrfguard.py`
- 新規 compose: `docker-compose.prod.yml`, `docker-compose.verify.yml`
- 環境変数例を拡充（`.env.example`, `.env.prod.example`）
- README のアクセス URL を proxy 経由に更新

---

## コミット一覧（`fc57e53`..HEAD）

| コミット | 概要 |
| --- | --- |
| `185ede3` | 監査ログ・TLS/proxy・利用者一括・モデル制御・チャット分離・データ削除スクリプト |
| `4c541f8` | 入力制限（ngword-app）・プロンプトテンプレート（prompt-app） |
| `cd35eb8` | SeaweedFS 成果物配信・dify-app artifact 再ホスト |
| `ee9b2da` | nginx 単一入口・内部 HMAC・SSRF 対策・RAG URL 取込・Form Spec v1 |

---

## 移行上の注意

- **アクセス URL** が `http://localhost:5173` / `:8000` から **`http://localhost/`（proxy 経由）** に変わります。
- **本番・閉域** では `.env.prod` と `docker-compose.prod.yml`（必要なら `docker-compose.verify.yml`）を使用してください。
- **`INTERNAL_SIGNING_SECRET`** は backend と全 exApp で同一値に設定してください（本番必須）。
- RAG のフォルダ階層モデル（中間版）は **タグ + URL モデル** に置き換えられています。

### 修正（レビュー反映）

- SeaweedFS に healthcheck を追加し、backend 起動を S3 準備完了後に待機
- `/exapps/history` の他者履歴参照（IDOR）を修正（本人の履歴のみ返却）
- 履歴削除を本人分のみに限定（システム管理者は従来どおり全件可）
- 管理者ツールチームのアプリを非システム管理者の一覧から明示除外
