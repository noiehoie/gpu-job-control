以下に、両ドキュメントと実ファイル（`scripts/runpod-asr-serverless-contract-probe.py` の `main()` 35–307 行付近、`scripts/vast-pyworker-serverless-contract-probe.py` の `main()` 24–331 行、`_lookup_template` 617–625、`_lookup_template_by_id` 628–636、`_resolve_template_record` 667–715、`_sdk_response_ok` 526–540、`_request_ok` 543–571、`_create_template` 381–398、`_create_endpoint` 401–415、`_disable_endpoint_rest` 615–621）に基づく基本計画をまとめる。

---

## 1. Gemini による訂正（事実誤認・危険提案）

**事実誤認（01 の記述を捨てる）**

- `scripts/runpod-asr-serverless-contract-probe.py` の `main()` について、「`if managed_resources:` に `submit` / `poll`、`cleanup`（`finally`）、`artifact` 集約まで詰まっている」という説明は**誤り**。実コードでは `_submit_payload`、`_serverless_request`、`_poll_serverless_run`、`try` / `except` / `finally` の cleanup、および `probe_payload` / `metrics` / `verify` / `_write_json` による artifact 集約は **`if managed_resources:` の外**で、managed / unmanaged の**共通後段**である（```137:307:scripts/runpod-asr-serverless-contract-probe.py```）。
- 「`_guard_clean(post_guard)` と `_startup_seconds_observed(run_result)` のローカル変数化で重複削減」は**採用しない**。副作用のない軽量参照であり、**行削減・可読性の根拠として不十分**（02）。

**危険提案（実施しない）**

- `scripts/vast-pyworker-serverless-contract-probe.py` の `_sdk_response_ok()`（`response.get("status")` と `response.get("response")`）と `_request_ok()`（`worker_request.get("http_status")` と `worker_request.get("body")` / ネストした `response`）を**共通ヘルパーに統合する案は却下**。キー構造が異なり、統合でステータス判定が漏れ **green を壊す**（02、```526:571:scripts/vast-pyworker-serverless-contract-probe.py```）。

**02 で追加された削減・整理（01 に無い）**

- `scripts/runpod-asr-serverless-contract-probe.py` の `_create_template()` と `_create_endpoint()` にある  
  `if any(token in RUNPOD_REST_BASE_URL for token in ("rest.runpod.io", "api.runpod.io/v1")):` の**完全重複**を、例として `_is_rest_api()` のような**1行述語のヘルパー**へ抽出する（```384:384:scripts/runpod-asr-serverless-contract-probe.py```、```402:402:scripts/runpod-asr-serverless-contract-probe.py```）。
- `main()` の `finally` 内の `disable_endpoint` / `delete_endpoint` の graphql / REST 分岐は、単純な if の寄せ集めではなく、例として `_disable_endpoint_by_surface()` のような**ラッパーへ押し出して `main()` から分岐を消す**方針が妥当（02。実装は後段でも可）。

---

## 2. 関数単位の「削減候補行数」（見積もり）

行数は**現在の関数定義の行範囲**と、リファクタ後の**おおまかな差分**（同一リポジトリ内の機械的見積もり）である。CI の green と無関係な「文字数削減」は含めない。

### `scripts/runpod-asr-serverless-contract-probe.py`（RunPod serverless）

| 関数名 | 定義行（現状） | 削減候補（行の見積もり） | 内容 |
|--------|----------------|---------------------------|------|
| `_disable_endpoint_rest` | 615–621（7 行） | **シグネチャと呼び出しから未使用 `template_id` を除去**（物理行は 0〜1。冗長 API の除去が主） | ```615:621:scripts/runpod-asr-serverless-contract-probe.py```、呼び出し ```180:180:scripts/runpod-asr-serverless-contract-probe.py``` |
| `_create_template` | 381–398（18 行） | **条件式 1 行をヘルパー 1 呼び出しに置換**；ファイル全体ではヘルパー定義 **+約 3 行**のため**純増しうる**。「削減」は重複条件の**単一ソース化**が主 | ```384:397:scripts/runpod-asr-serverless-contract-probe.py``` |
| `_create_endpoint` | 401–415（15 行） | 上と同様（**+ヘルパー分の相殺**） | ```402:414:scripts/runpod-asr-serverless-contract-probe.py``` |
| `main` | 35–307（273 行） | **分岐のラッパー化**で `main` から **約 12 行**（`finally` の disable/delete の graphql/else 2 ブロック）を移動可能。`_guard_clean` / `_startup_seconds_observed` のローカル化は**候補に含めない**（02） | ```175:192:scripts/runpod-asr-serverless-contract-probe.py``` |
| `_prepare_managed_template` | 635–688 | **今回の「行削減」第一候補から外す**（責務分割は後回し） | 01 の深い分割は後段 |

### `scripts/vast-pyworker-serverless-contract-probe.py`（Vast serverless）

| 関数名 | 定義行（現状） | 削減候補（行の見積もり） | 内容 |
|--------|----------------|---------------------------|------|
| `_lookup_template` | 617–625（9 行） | 内部共通化により **この関数ブロックは 2 行程度の薄いラッパー**に縮小可能 | ```617:625:scripts/vast-pyworker-serverless-contract-probe.py``` |
| `_lookup_template_by_id` | 628–636（9 行） | 同上 | ```628:636:scripts/vast-pyworker-serverless-contract-probe.py``` |
| 上記ペア合算 | 18 行 | **共通実装 約 7 行 + ラッパー 2+2 行 ≒ 11 行** → **ファイル全体で約 7 行削減**の見積もり | `_run_vast_search_templates_first(query: str)` 等 |
| `main` | 24–331（308 行） | `_read_payload` の**事前バインド**で **呼び出し行の重複や条件付き読みの整理**、コード行 **0〜2**、**主目的は同一パス時の I/O 重複排除**（02） | ```146:146:scripts/vast-pyworker-serverless-contract-probe.py```、```162:162:scripts/vast-pyworker-serverless-contract-probe.py```、```172:172:scripts/vast-pyworker-serverless-contract-probe.py``` |
| `_resolve_template_record` | 667–715（49 行） | `_write_json(..., "vast_template_lookup.json")` パターンのヘルパー化は **可読性・漏れ防止**が主で、**純行数は 0〜2**程度 | ```675:676:scripts/vast-pyworker-serverless-contract-probe.py``` 等 4 箇所 |
| `_sdk_response_ok` | 526–540 | **変更しない**（共通化しない） | 02 |
| `_request_ok` | 543–571 | **変更しない**（共通化しない） | 02 |

---

## 3. 今やるべき最小安全集合と後回し

### 最小安全集合（外部挙動・artifact キー・cleanup 意味を変えにくい順）

1. **`scripts/runpod-asr-serverless-contract-probe.py`**  
   - `_disable_endpoint_rest` から未使用キーワード専用引数 `template_id` を削除し、```180:180:scripts/runpod-asr-serverless-contract-probe.py``` の `template_id=str(endpoint["templateId"])` を削除する。
2. **`scripts/runpod-asr-serverless-contract-probe.py`**  
   - `_create_template` と `_create_endpoint` の REST 判定行を **`_is_rest_api()`（仮名）** に抽出する。GraphQL/REST の分岐結果は変えない。
3. **`scripts/vast-pyworker-serverless-contract-probe.py`**  
   - `_lookup_template` と `_lookup_template_by_id` を **クエリ文字列を受け取る単一内部関数**に集約し、公開シグネチャは維持する（01・02 一致）。
4. **`scripts/vast-pyworker-serverless-contract-probe.py`**  
   - `main` 内の `_read_payload` を、**同一ファイルパスで複数回読まない**ように変数へ束縛する（02）。`worker_request` / `route` の意味的に別物のペイロードは無理にマージしない。

### 後回し（green・契約検証の後、または実施しない）

- `_sdk_response_ok` と `_request_ok` の**共通化** → **実施しない**（02）。
- `_guard_clean` / `_startup_seconds_observed` のローカル変数化のみを目的とした変更 → **実施しない**（02）。
- 両 `main` の**大規模な関数分割**（provision / template / request / cleanup / artifact の切り出し）→ **最小集合の green 確認後**（01 の 5 番目以降）。
- `_resolve_template_record` の **lookup と artifact 書き出しの責務分離の深掘り** → **後回し**（純行削減が小さいうえリグレッション余地あり）。
- `_gpu_probe_from_request` のシグナチャ拡張（01 にあった案）→ **後回し**（未合意）。
- `routing_by_module_enabled` の導入・変更 → **両スクリプトのスコープ外**（01 の触禁のまま）。

---

## 4. 変更対象ファイル・関数名・変更しない関数名

### 変更対象ファイル

- `scripts/runpod-asr-serverless-contract-probe.py`
- `scripts/vast-pyworker-serverless-contract-probe.py`

### 変更する関数（最小安全集合）

- `scripts/runpod-asr-serverless-contract-probe.py`: `_disable_endpoint_rest`、`main`（呼び出し行のみ）、`_create_template`、`_create_endpoint`、**新規** `_is_rest_api`（仮名）
- `scripts/vast-pyworker-serverless-contract-probe.py`: `main`（`_read_payload` 周り）、`_lookup_template`、`_lookup_template_by_id`、**新規** 内部検索ヘルパー（例: `_run_vast_search_templates_first`）

### 変更しない関数（最小安全集合の段階）

**RunPod:** `_submit_payload`、`_normalized_output`、`_poll_serverless_run`、`_blocker_chain`、`_serverless_request`、`_create_template_graphql`、`_create_endpoint_graphql`、RunPodProvider の `cost_guard` / `plan_asr_endpoint` / `_endpoint_health_sample` / `_disable_endpoint` / `_delete_endpoint` の**呼び出し順序**、`_delete_ok` の意味、`cleanup["ok"]` の式の論理。

**Vast:** `_sdk_request`、`_route_probe`、`_sdk_response_ok`、`_request_ok`、`_cleanup_ok`、artifact のトップレベルキー（`result` / `metrics` / `verify` / `probe_info` のスキーマ）。

---

## 5. 実装順・検証順

**実装順**

1. `scripts/runpod-asr-serverless-contract-probe.py` — `_disable_endpoint_rest` とその唯一の呼び出し `main` の `finally`（```175:192:scripts/runpod-asr-serverless-contract-probe.py```）。
2. `scripts/runpod-asr-serverless-contract-probe.py` — `_is_rest_api` 追加と `_create_template` / `_create_endpoint` の置換。
3. `scripts/vast-pyworker-serverless-contract-probe.py` — `_lookup_template` / `_lookup_template_by_id` の共通化。
4. `scripts/vast-pyworker-serverless-contract-probe.py` — `main` の `_read_payload` キャッシュ。

**検証順（各ステップのあと）**

1. 該当スクリプトの **構文チェック**（例: `python -m py_compile` 対象ファイル）。
2. リポジトリが定義している **serverless contract / green** のテストまたは CI ジョブ（プロジェクトの `Makefile` / `pytest` / ドキュメントに記載のコマンド）を **RunPod 用・Vast 用それぞれ**実行できる範囲で実行。
3. 変更ステップごとに **artifact のファイル名一覧**（`runpod_serverless_*.json` / `vast_*.json` / `result.json` 等）が変わっていないことを確認。

**後続実装順（最小集合の green 後）**

5. `scripts/runpod-asr-serverless-contract-probe.py` — `_disable_endpoint_by_surface` / `_delete_endpoint_by_surface`（仮名）で `main` の cleanup 分岐を移動（02）。
6. `scripts/vast-pyworker-serverless-contract-probe.py` — `_resolve_template_record` の lookup 書き出しヘルパー（任意）。
7. 両 `main` の構造分割（01 の後半）。

---

## 6. GPT-5.4 High / Gemini / Claude へ再審査させるべき論点一覧

1. **`if managed_resources:` の正確な責務境界**（訂正後の説明がコード ```109:132:scripts/runpod-asr-serverless-contract-probe.py``` と一致するか）。
2. **`_is_rest_api()` の配置**（モジュール定数 `RUNPOD_REST_BASE_URL` との関係、将来 REST URL が増えたときの単一修正点として十分か）。
3. **`_disable_endpoint_rest` のシグネチャ変更**が、型チェッカ・他スクリプトからの import 有無に波及しないか（現状 grep では当該リポ内呼び出しは `main` の 1 箇所のみ）。
4. **`main`（Vast）の `_read_payload` キャッシュ**における、**`args.worker_request_file or args.route_payload_file`** と **`args.route_payload_file`** と **`args.worker_request_file`** の**全パス組合せ**で、従来と同じバイト列が各呼び出し先に渡るか。
5. **`_lookup_template` 統合**後も **`vast_template_lookup.json` の内容と書き込みタイミング**が `_resolve_template_record` の各分岐で不変か。
6. **`cleanup["steps"]` の辞書キー**（`disable_endpoint` / `delete_endpoint` 等）をラッパー移動後も**完全一致**で維持するか（02 のラッパー方針の検証）。
7. **`_sdk_response_ok` / `_request_ok` を触らない**前提で、`verify.json` の `worker_request_ok` 等の意味が変わらないことの再確認。
8. **Council ゲート**（`AGENTS.md` の research / design / code / audit と `scripts/validate_council_audit.py`）に、この変更セットをどの **task-id** で記録するか。
9. **02 の結論「Composer2 にそのまま渡すな」**が、上記訂正・却下を反映した計画で**解除可能か**の最終判断。

---

**補足（Ask モード）:** ここまでは読取と整理のみである。パッチ適用や council 記録の実行が必要なら Agent モードに切り替えてほしい。
