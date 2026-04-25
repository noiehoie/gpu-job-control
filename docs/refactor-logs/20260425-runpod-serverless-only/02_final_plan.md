結論。

対象は `scripts/runpod-asr-serverless-contract-probe.py` のみ。

## 採用

1. import 3行を 1行へ統合
2. `_official_template_smoke_ok` を同値な単一 `return` に短縮
3. `_guard_clean` を同値な単一 `return` に短縮
4. `_delete_template` の一時変数 `result` を削除
5. `_delete_endpoint_rest` の一時変数 `result` を削除

## 不採用

1. `create_surface` の削除
   - `managed_resources` の途中更新をまたぐため、局所変数のままの方が安全
2. `_probe_name_for_contract` の削除
   - 既存テストが直接参照
3. `_audio_base64` のインライン化
   - 行数利益が小さく、可読性が落ちる
4. REST/GraphQL 分岐の再整理
   - 利益より回帰リスクが大きい

## 検証

1. `uv run python -m py_compile scripts/runpod-asr-serverless-contract-probe.py`
2. `uv run pytest -q tests/test_runpod_serverless_probe_script.py tests/test_vast_serverless_probe_script.py tests/test_provider_contract_probe.py tests/test_launch_gate.py`
3. `uv run --with ruff ruff check scripts/runpod-asr-serverless-contract-probe.py tests/test_runpod_serverless_probe_script.py`
