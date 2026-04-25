実コードと基本計画を突き合わせて監査する。

---

## 監査結果

### 1. 基本計画の事実誤認

**1.1 `_disable_endpoint_rest` のシグネチャ説明は正しいが、`template_id` 削除の影響評価が不完全**

- 実コード: `scripts/runpod-asr-serverless-contract-probe.py` 615–621 行で `template_id` は受け取るのみで本体未使用（事実）。
- 呼び出し元: 180 行の **1 箇所のみ**（事実）。
- ただし基本計画の「物理行は 0〜1」は**過小**。シグネチャから `*, template_id: str` を削れば、関数定義側で**1 行（615 行末の引数）**、呼び出し側で**1 行（180 行末の `template_id=...`）**の **2 行削減**が確定する。誤認というより**見積過少**。

**1.2 「`if managed_resources:` の責務境界」訂正は正しい**

- 137–192 行（submit/poll/cancel/finally）は `if managed_resources:` の**外**にあることをコードで確認した（137 行は `if managed_resources:` ブロックの外、`try` 直下の続き）。基本計画の訂正に**事実誤認なし**。

**1.3 `_create_template` / `_create_endpoint` の REST 判定重複箇所は正しい**

- 384 行と 402 行の `if any(token in RUNPOD_REST_BASE_URL for token in ("rest.runpod.io", "api.runpod.io/v1")):` は**完全一致**で重複（事実）。

**1.4 `_lookup_template` / `_lookup_template_by_id` の重複構造は正しい**

- 617–625 行と 628–636 行は `query` 文字列の組み立て（`hash_id == ` vs `id == `）以外は**完全一致**（事実）。

**1.5 「Vast `main` の `_read_payload` を変数束縛」候補は要再評価**

- 実コード 146 行 `_read_payload(args.worker_request_file or args.route_payload_file)`、162 行 `_read_payload(args.route_payload_file)`、172 行 `_read_payload(args.worker_request_file)`。
- **3 箇所のキーがすべて異なる**（`A or B` / `B` / `A`）。同一引数で重複呼び出しされている箇所は**ない**。基本計画の「同一パス時の I/O 重複排除」は**条件付きでしか発生しない**（`A == B` のときのみ）。事実誤認ではないが、**削減根拠が弱い**。

---

### 2. 過剰なリファクタ案の却下

却下するもの（基本計画の「最小安全集合」内に潜む過剰案）:

- **却下 A**: 基本計画 4 番「Vast `main` の `_read_payload` キャッシュ」。3 引数の意味が違うため、安全な統合は `args.worker_request_file == args.route_payload_file` のときのみ。条件分岐を増やす変更は**コード行が増える可能性**があり、green を壊す余地もある。**最小集合から外す**。
- **却下 B**: 基本計画「後段」の `_disable_endpoint_by_surface` / `_delete_endpoint_by_surface` ラッパー化（177–192 行の `if args.managed_create_surface == "graphql":` 4 分岐を関数に押し出す案）。`provider._disable_endpoint` / `provider._delete_endpoint` を内部で呼ぶラッパーは**事実上 provider adapter 境界を抽象化**することになり、`AGENTS.md` の「provider adapter は触らない」の精神に近接する。**実施しない**。
- **却下 C**: 基本計画「後段」の `_resolve_template_record` の `_write_json(..., "vast_template_lookup.json")` 4 箇所（676/681/688/693）のヘルパー化。純行削減 0〜2 で**書き込みタイミングの不変性検証コスト**が大きい。**実施しない**。
- **却下確認**: 基本計画自身が却下している `_sdk_response_ok` / `_request_ok` 統合、`_guard_clean` / `_startup_seconds_observed` のローカル変数化は、**却下が正しい**。コード上、両者のキー構造（`response.get("status")` vs `worker_request.get("http_status")` + ネスト `response`）は別物で、統合は green を壊す。

---

### 3. 最小安全候補（優先順位付き）

green / artifact / cleanup の意味を**変えない**ことが確実な順:

| 優先 | ファイル | 関数 | 行種別 | 内容 |
|---|---|---|---|---|
| **P1** | `scripts/runpod-asr-serverless-contract-probe.py` | `_disable_endpoint_rest`（定義 615 行）と `main`（呼び出し 180 行） | 定義行 615 / 呼び出し行 180 | `*, template_id: str` 引数を関数定義から削除し、呼び出し側 `template_id=str(endpoint["templateId"])` も削除。本体未使用のため副作用なし。確定 2 行削減。 |
| **P2** | `scripts/runpod-asr-serverless-contract-probe.py` | 新規 `_is_rest_api`（仮名、`RUNPOD_REST_BASE_URL` 定義直下に追加）、`_create_template`（384 行）、`_create_endpoint`（402 行） | 新規定義行 + 置換行 ×2 | 384 行と 402 行の `if any(...)` を `if _is_rest_api():` に置換。条件式の単一ソース化。純行数は概ね相殺だが**重複の単一化**が目的。 |
| **P3** | `scripts/vast-pyworker-serverless-contract-probe.py` | 新規内部ヘルパー（例 `_search_template_first(query: str)`）、`_lookup_template`（617 行）、`_lookup_template_by_id`（628 行） | 新規定義行 + 既存関数の本体置換 | 共通実装にクエリ文字列を渡す形へ集約。公開シグネチャ（引数名・戻り値型）は維持。`vastai search templates` の呼び出し回数・引数は不変。 |

**優先順位の根拠:**
- P1: 未使用引数の削除のみで、挙動・artifact・cleanup ステップキー（`disable_endpoint`）に**影響なし**。
- P2: 分岐結果は同一。`_is_rest_api()` の戻り値は判定式と数学的に等価。GraphQL 経路のテストは `args.managed_create_surface == "graphql"` で別経路のため、REST 判定の境界は変わらない。
- P3: 公開シグネチャ維持なら呼び出し元（`_resolve_template_record` 674/679/686/691 行）は不変。ただし共通化の戻り値型・空時の挙動（`{}` 返却）を**完全一致**で揃える必要あり。

**最小集合から外したもの:**
- 基本計画 4 番（Vast `_read_payload` 束縛） → 却下 A により除外。
- 基本計画 5 番以降（cleanup ラッパー、main 構造分割） → 却下 B / 後回し。

---

### 4. 変更しない関数（最小集合の段階）

`scripts/runpod-asr-serverless-contract-probe.py`:
- `main` の **`finally` 分岐構造**（174–205 行）、`_submit_payload`（532 行）、`_serverless_request`（476 行）、`_poll_serverless_run`（765 行）、`_normalized_output`（819 行）、`_blo
...[truncated 1767 chars]
