結論。

`serverless` 2系統の次の一手は、**2件だけ**に絞る。  
`all green` 維持を最優先にすると、ここから先の候補は広げない。

補足:
- `04_gpt54_review.md` と `06_claude_review.md` は一致した。
- `05_gemini_review.md` は `check_pod_health` / `verify_contract` / `get_instance_status` など、対象ファイルに存在しない関数名を挙げており、**最終計画の根拠には採用しない**。

## 今回やる最小安全集合

1. `scripts/runpod-asr-serverless-contract-probe.py`
   - 対象:
     - `_disable_endpoint_rest` 定義行
     - `main` の唯一の呼び出し行
   - 変更:
     - `_disable_endpoint_rest(api_key, endpoint_id, *, template_id)` から未使用 `template_id` を削除
     - `main` 側の `template_id=str(endpoint["templateId"])` を削除
   - 根拠:
     - 本体未使用
     - 呼び出しは 1 箇所のみ
     - `cleanup["steps"]` のキー、payload、戻り値 shape を不変にできる
   - 期待削減:
     - **2 行**

2. `scripts/vast-pyworker-serverless-contract-probe.py`
   - 対象:
     - `_lookup_template`
     - `_lookup_template_by_id`
   - 変更:
     - 内部共通ヘルパー 1 本に寄せる
     - 公開シグネチャは維持する
     - `allow_failure=True`
     - 先頭要素採用
     - 空時 `{}` fallback
     - `vastai search templates` の引数と回数を不変に保つ
   - 根拠:
     - 2 関数は query 文字列以外が完全重複
     - 呼び出し側 `_resolve_template_record` の分岐を崩さずに圧縮できる
   - 期待削減:
     - **約 7 行**

## 今回やらない項目

1. `scripts/runpod-asr-serverless-contract-probe.py`
   - `_is_rest_api()` 抽出
   - cleanup 分岐のラッパー化
   - `main` の大規模分割

2. `scripts/vast-pyworker-serverless-contract-probe.py`
   - `main` の `_read_payload` キャッシュ
   - `_resolve_template_record` 内の `_write_json(..., "vast_template_lookup.json")` 抽出
   - `_sdk_response_ok` / `_request_ok` の共通化

理由:
- 行削減が小さい
- 条件分岐や書き込みタイミングの差分が増える
- `all green` を崩す可能性に対して利益が小さい

## 関数単位の削減候補一覧

1. `scripts/runpod-asr-serverless-contract-probe.py`
   - `_disable_endpoint_rest`: 1 行
   - `main` の呼び出し行: 1 行

2. `scripts/vast-pyworker-serverless-contract-probe.py`
   - `_lookup_template`: 薄いラッパー化
   - `_lookup_template_by_id`: 薄いラッパー化
   - 新規内部ヘルパー追加後のファイル全体純減: 約 7 行

## 実装順序

1. RunPod:
   - `_disable_endpoint_rest` の未使用引数削除
   - `main` の呼び出し修正
2. Vast:
   - 内部検索ヘルパー追加
   - `_lookup_template` を薄いラッパーへ変更
   - `_lookup_template_by_id` を薄いラッパーへ変更

## 検証順序

1. `uv run python -m py_compile scripts/runpod-asr-serverless-contract-probe.py scripts/vast-pyworker-serverless-contract-probe.py`
2. `uv run pytest -q tests/test_runpod_serverless_probe_script.py tests/test_vast_serverless_probe_script.py tests/test_provider_contract_probe.py tests/test_launch_gate.py`
3. `uv run --with ruff ruff check scripts/runpod-asr-serverless-contract-probe.py scripts/vast-pyworker-serverless-contract-probe.py tests/test_runpod_serverless_probe_script.py tests/test_vast_serverless_probe_script.py`

## 変更禁止領域

1. `src/gpu_job/providers/*`
2. `src/gpu_job/launch_gate.py`
3. `config/execution-policy.json`
4. `routing_by_module_enabled`
5. `cleanup["steps"]` のキー名
6. artifact top-level schema
