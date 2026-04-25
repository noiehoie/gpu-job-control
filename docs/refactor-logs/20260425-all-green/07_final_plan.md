## 結論
3文書を現状事実に合わせて統合した結果、採用する変更は1件だけである。

採用変更:
- `tests/test_provider_contract_probe.py` に回帰固定テスト `test_default_contract_probes_keep_required_shape_for_plan_and_canary_job()` を追加する。
- `src/gpu_job/provider_contract_probe.py` の `DEFAULT_CONTRACT_PROBES` だけを、同一ファイル内の file-local base dict 3個で整理する。

採用しない提案:
- `parse_contract_probe_artifact()` 分割
- `_workspace_contract_summary()` 表駆動化
- `workspace_observation_coverage()` ループ化
- `_canary_job()` の provider 別分割
- `launch_gate.py` の `_slice_status()` / `_slice_blocks()` 定数化
- `plan_quote` 系の DRY 化
- 新規モジュール `provider_contract_probe_helpers.py` 作成
- `src/gpu_job/providers/*` の変更

Claude 文書の「all green ではない」は stale 前提なので棄却する。Gemini 文書の「過剰な抽象化は禁止」は採用する。したがって、`dict` アンパックで共有するのは immutable な scalar 値だけに限定する。`required_files` と `forbidden_models` は共有しない。`contract_probe_spec()` は `dict(spec)` の shallow copy を取り、`_enrich_probe_spec()` は spec を更新するため、mutable 値の共有は採用しない。

## 変更対象ファイル
- `tests/test_provider_contract_probe.py`
  - 追加するテスト名: `test_default_contract_probes_keep_required_shape_for_plan_and_canary_job`
  - テスト内容を固定する対象キー:
    - 必須キー: `provider`, `provider_module_id`, `workload_family`, `job_type`, `gpu_profile`, `expected_model`, `expected_image`, `expected_image_digest`, `forbidden_models`, `required_files`, `require_gpu_utilization`, `cache_required`
    - 条件付きキー: `workspace_contract_required`, `image_contract_id`, `serverless_handler_contract_required`, `official_template_smoke_required`
  - テスト手順:
    1. `for probe_name, spec in DEFAULT_CONTRACT_PROBES.items()` で全件を走査
    2. `required_keys.issubset(spec.keys())` を確認
    3. 条件付きキーが存在する場合だけ型を固定する
       - `workspace_contract_required`: `bool`
       - `image_contract_id`: `str`
       - `serverless_handler_contract_required`: `bool`
       - `official_template_smoke_required`: `bool`
    4. `planned = plan_contract_probe(spec["provider"], probe_name)` を実行
    5. `job = _canary_job(spec)` を実行
    6. `planned["probe_name"] == probe_name`
    7. `planned["provider"] == spec["provider"]`
    8. `planned["spec"]["provider_module_id"] == spec["provider_module_id"]`
    9. `job.provider == spec["provider"]`
    10. `job.gpu_profile == spec["gpu_profile"]`

- `src/gpu_job/provider_contract_probe.py`
  - `DEFAULT_CONTRACT_PROBES` の直前に次の3定数を追加する
    - `_RUNPOD_SERVERLESS_PROBE_BASE`
    - `_LLM_HEAVY_PROBE_BASE`
    - `_ASR_DIARIZATION_PROBE_BASE`
  - 具体的な定数内容:
    - `_RUNPOD_SERVERLESS_PROBE_BASE = {"provider": "runpod", "provider_module_id": RUNPOD_SERVERLESS}`
    - `_LLM_HEAVY_PROBE_BASE = {"workload_family": "llm_heavy", "job_type": "llm_heavy", "gpu_profile": "llm_heavy", "expected_image_digest": "", "require_gpu_utilization": True}`
    - `_ASR_DIARIZATION_PROBE_BASE = {"workload_family": "asr", "job_type": "asr", "gpu_profile": "asr_diarization", "expected_model": "pyannote/speaker-diarization-3.1", "expected_image_digest": "", "require_gpu_utilization": False}`
  - これらを適用する probe 名:
    - `_RUNPOD_SERVERLESS_PROBE_BASE`
      - `runpod.llm_heavy.endpoint_openai`
      - `runpod.serverless.heartbeat`
      - `runpod.asr_diarization.serverless_handler`
      - `runpod.asr.official_whisper_smoke`
    - `_LLM_HEAVY_PROBE_BASE`
      - `modal.llm_heavy.qwen2_5_32b`
      - `runpod.llm_heavy.endpoint_openai`
      - `runpod.llm_heavy.pod_http`
    - `_ASR_DIARIZATION_PROBE_BASE`
      - `vast.asr_diarization.pyannote`
      - `modal.asr_diarization.pyannote`
      - `runpod.asr_diarization.pyannote`
      - `runpod.asr_diarization.serverless_handler`
  - literal のまま残すキー:
    - `expected_image`
    - `forbidden_models`
    - `required_files`
    - `cache_required`
    - `workspace_contract_required`
    - `image_contract_id`
    - `serverless_handler_contract_required`
    - `official_template_smoke_required`
    - `provider_module_id` が `RUNPOD_SERVERLESS` 以外の entry 固有値
  - literal のまま残す probe 名:
    - `vast.instance_smoke.cuda`
    - `vast.asr.serverless_template`

## 変更しないファイル
- `src/gpu_job/launch_gate.py`
- `src/gpu_job/execution_record.py`
- `src/gpu_job/contracts.py`
- `src/gpu_job/workflow.py`
- `src/gpu_job/runner.py`
- `src/gpu_job/requirements.py`
- `src/gpu_job/workspace_registry.py`
- `src/gpu_job/provider_module_contracts.py`
- `src/gpu_job/providers/*`
- `tests/test_provider_module_contracts.py`
- `tests/test_launch_gate.py`
- `tests/test_policy_router.py`
- `docs/launch-slice-manifest.json`
- `config/execution-policy.json`

変更しない関数:
- `parse_contract_probe_artifact()`
- `_workspace_contract_summary()`
- `workspace_observation_coverage()`
- `_canary_job()`
- `_slice_status()`
- `_slice_blocks()`
- `_plan_quote_from_job()`
- `_workflow_plan_quote()`
- `_local_helper_plan_quote()`
- `runner._plan_quote()`

維持する制約:
- `routing_by_module_enabled=False`
- provider adapter 非接触
- probe 名の後方互換維持
- 新規ファイル追加禁止
- builder 関数追加禁止
- loop 生成禁止
- class 化禁止

## 実装順
1. task id を `20260425-provider-contract-probe-static-dedup` に固定し、council の `research` と `design` を記録する。
2. ベースラインとして `uv run python -m pytest -q` と `uv run gpu-job selftest` を実行し、all green を記録する。
3. `tests/test_provider_contract_probe.py` に `test_default_contract_probes_keep_required_shape_for_plan_and_canary_job()` を追加する。
4. 追加した新テストだけを実行する。
5. `src/gpu_job/provider_contract_probe.py` に 3 個の base dict を追加し、`DEFAULT_CONTRACT_PROBES` の対象 entry だけを `**` 展開へ置き換える。
6. 既存関数の本体には手を入れない。
7. focused test を順番どおりに実行する。
8. 全量テストと `selftest` を実行する。
9. council の `code` と `audit` を記録し、`uv run python scripts/validate_council_audit.py --task-id 20260425-provider-contract-probe-static-dedup` を実行する。

## 検証順
1. `uv run python -m pytest -q tests/test_provider_contract_probe.py::ProviderContractProbeTest::test_default_contract_probes_keep_required_shape_for_plan_and_canary_job`
2. `uv run python -m pytest -q tests/test_provider_contract_probe.py::ProviderContractProbeTest::test_vast_pyworker_canary_does_not_use_direct_instance_fallback`
3. `uv run python -m pytest -q tests/test_provider_contract_probe.py::ProviderContractProbeTest::test_runpod_serverless_asr_handler_probe_is_distinct`
4. `uv run python -m pytest -q tests/test_provider_contract_probe.py::ProviderContractProbeTest::test_runpod_serverless_official_whisper_smoke_probe_is_distinct`
5. `uv run python -m pytest -q tests/test_provider_module_contracts.py::test_contract_probe_specs_expose_module_probe_names_without_renaming_probe`
6. `uv run python -m pytest -q tests/test_launch_gate.py::test_launch_phase_gate_accepts_official_runpod_serverless_probe`
7. `uv run python -m pytest -q tests/test_provider_module_contracts.py::test_provider_module_routing_flag_schema_is_design_only_and_disabled`
8. `uv run python -m pytest -q tests/test_policy_router.py::PolicyAndRouterTest::test_provider_module_routing_policy_is_design_only_and_must_remain_disabled`
9. `uv run python -m pytest -q tests/test_provider_contract_probe.py tests/test_provider_module_contracts.py tests/test_launch_gate.py tests/test_policy_router.py`
10. `uv run python -m pytest -q`
11. `uv run gpu-job selftest`

失敗時の規則:
- 1件でも fail した時点で次へ進まない。
- 失敗した状態で追加のリファクタはしない。
- provider adapter 修正へ拡張しない。
- `routing_by_module_enabled` を変更しない。

この計画で実装してよい。
