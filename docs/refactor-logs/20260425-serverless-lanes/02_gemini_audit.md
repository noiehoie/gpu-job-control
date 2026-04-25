以下の監査結果を報告する。

### 事実誤認の指摘
1. `scripts/runpod-asr-serverless-contract-probe.py` の `main()` について、「`managed_resources` 分岐は、`plan決定`、`template準備`、`endpoint準備`、`submit/poll`、`cleanup`、`artifact集約` を1関数に詰めている」は**誤り**。
   実際には `submit/poll`、`cleanup` (finally句)、および `artifact集約` は `if managed_resources:` ブロックの**外側**に位置しており、managed / unmanaged (existing) の両フローで共通実行される。ブロックの責務と `main()` 全体のフローを完全に混同している。
2. 同 `main()` について、「`_guard_clean(post_guard)`、`_startup_seconds_observed(run_result)` は複数回計算している。ローカル変数化で重複を減らせる」という指摘は過剰。これらは副作用のない非常に軽量な辞書参照関数であり、ローカル変数化による可読性向上や計算コスト削減のメリットは皆無に等しい。これを「重複」として扱うのは不適切。

### 見落としている削減候補の追加
1. **`scripts/runpod-asr-serverless-contract-probe.py`**:
   `_create_template()` と `_create_endpoint()` の内部で、REST APIかどうかの判定ロジック `if any(token in RUNPOD_REST_BASE_URL for token in ("rest.runpod.io", "api.runpod.io/v1")):` が完全に重複している。これを `_is_rest_api()` などの小ヘルパー関数に抽出して重複を削るべき。
2. **`scripts/runpod-asr-serverless-contract-probe.py`**:
   `main()` 内の `cleanup` ステップにおける `disable_endpoint` と `delete_endpoint` の `graphql/rest` 分岐は、単純な `if/else` 集約ではなく、`_disable_endpoint_by_surface()` のようなラッパー関数に押し込むことで `main()` の中から分岐自体を完全に消し去るべき。

### green を壊す危険提案
1. **`scripts/vast-pyworker-serverless-contract-probe.py`**:
   「`_sdk_response_ok()` と `_request_ok()` の共通ヘルパー化」は**極めて危険なため却下せよ**。
   これらは一見同じHTTPステータス判定をしているように見えるが、参照するキーが異なる。
   - `_request_ok()` は `worker_request.get("http_status")` を評価する。
   - `_sdk_response_ok()` は `response.get("status")` を評価する。
   不用意に統合すると、キーの違いを吸収できずにステータス判定が漏れ、確実に green のテストを壊す。

### 今やるべき最小安全候補
各ファイルにおける、外部挙動を変えずに即座に実施できる安全な第一歩は以下の通り。

- **`scripts/runpod-asr-serverless-contract-probe.py`**:
  `_disable_endpoint_rest()` の未使用引数 `template_id` を削除する。および、上述した `_create_template()` と `_create_endpoint()` の URL判定重複をヘルパーに抽出する。
- **`scripts/vast-pyworker-serverless-contract-probe.py`**:
  完全重複している `_lookup_template()` と `_lookup_template_by_id()` を、検索クエリを受け取る単一のヘルパー関数（例: `_run_vast_search_templates(query: str)`）に統合する。および、`main()` で複数回発生している `_read_payload(args.worker_request_file)` 等のディスクI/Oを変数に事前キャッシュして削る。

### このまま Composer2 に渡してよいか
**no**

（理由: 事実誤認を含み、かつキーの不整合を見落とした危険な共通化提案（`_sdk_response_ok` / `_request_ok`）が含まれているため。本監査内容を反映・修正した上で渡さなければならない。）
