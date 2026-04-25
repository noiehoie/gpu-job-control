## 結論
最短で安全にコード量を減らせる主戦場は `src/gpu_job/provider_contract_probe.py` と、その周辺の記録・契約投影層です。`src/gpu_job/providers/runpod.py` と `src/gpu_job/providers/vast.py` は削減余地が最大ですが、現条件の `provider_adapter_diff` 空維持に反するため、今回の実施計画では「分析対象だが着手禁止」です。

この報告は読取ベースです。Ask mode のため変更・テスト再実行はしていません。

## 1. 対象ファイル一覧
- 設計・制約: `README.md`, `docs/architecture.md`, `docs/routing-policy.md`, `docs/provider-module-contracts.md`, `docs/launch-phase0-5-gate.md`, `docs/launch-slice-manifest.json`
- 実行ポリシー・契約設定: `config/execution-policy.json`, `config/requirement-registry.json`, `config/provider-operations.example.json`, `config/image-contracts.json`
- 契約・記録・ルーティング中核: `src/gpu_job/contracts.py`, `src/gpu_job/execution_plan.py`, `src/gpu_job/execution_record.py`, `src/gpu_job/plan_quote.py`, `src/gpu_job/provider_catalog.py`, `src/gpu_job/provider_contract_probe.py`, `src/gpu_job/provider_module_contracts.py`, `src/gpu_job/provider_probe.py`, `src/gpu_job/requirements.py`, `src/gpu_job/workspace_registry.py`, `src/gpu_job/policy.py`, `src/gpu_job/policy_engine.py`, `src/gpu_job/router.py`, `src/gpu_job/workflow.py`, `src/gpu_job/error_class.py`, `src/gpu_job/verify.py`, `src/gpu_job/image_contracts.py`
- Provider 実装: `src/gpu_job/providers/__init__.py`, `src/gpu_job/providers/base.py`, `src/gpu_job/providers/modal.py`, `src/gpu_job/providers/runpod.py`, `src/gpu_job/providers/vast.py`
- Worker / Modal 実装: `src/gpu_job/workers/asr.py`, `src/gpu_job/modal_smoke.py`, `src/gpu_job/modal_asr.py`, `src/gpu_job/modal_llm.py`, `src/gpu_job/modal_vlm.py`
- Provider 外部プローブ script: `scripts/runpod-asr-serverless-contract-probe.py`, `scripts/vast-pyworker-serverless-contract-probe.py`
- 回帰契約テスト: `tests/test_provider_module_contracts.py`, `tests/test_provider_contract_probe.py`, `tests/test_launch_gate.py`, `tests/test_policy_router.py`, `tests/test_provider_catalog_contracts.py`, `tests/test_asr_diarization_contract.py`, `tests/test_image_distribution.py`, `tests/test_modal_provider.py`, `tests/test_modal_llm_quality.py`, `tests/test_runpod_config.py`, `tests/test_runpod_serverless_asr.py`, `tests/test_vast_asr_provider.py`, `tests/test_runpod_serverless_probe_script.py`, `tests/test_vast_serverless_probe_script.py`

## 2. 行単位で見た冗長箇所・重複箇所・責務混在箇所
- `src/gpu_job/provider_contract_probe.py`
  - `DEFAULT_CONTRACT_PROBES` に、全 probe で共通の `required_files`, `expected_image_digest`, `forbidden_models`, `cache_required` の初期値が何度も手書きされています。ここは最優先の重複削減箇所です。
  - `parse_contract_probe_artifact()` が 1 関数で、ファイル読込、artifact verify、observed 値抽出、workspace coverage 集計、check 判定、failure 分類、module canary evidence 生成、record 組立を同時に実行しています。責務が 1 箇所に集中しすぎています。

```352:420:src/gpu_job/provider_contract_probe.py
def parse_contract_probe_artifact(
    artifact_dir: str | Path,
    *,
    provider: str = "",
    probe_name: str = "",
    spec: dict[str, Any] | None = None,
    execution_mode: str = "fixture",
    append: bool = False,
) -> dict[str, Any]:
    path = Path(artifact_dir)
    spec = dict(spec or contract_probe_spec(provider, probe_name))
    provider = str(provider or spec["provider"])
    required = list(spec.get("required_files") or DEFAULT_REQUIRED)
    text = _artifact_text(path)
    result = _read_json(path / "result.json")
    metrics = _read_json(path / "metrics.json")
    verify_payload = _read_json(path / "verify.json")
    probe_info = _read_json(path / "probe_info.json")
    submit_result = _read_json(path / "submit_result.json")
    artifact_verify = verify_artifacts(
        path,
        required=required,
        require_manifest=bool(spec.get("require_manifest", False)),
        require_gpu_utilization=bool(spec.get("require_gpu_utilization", False)),
        execution_class="gpu",
    )
    observed_model = _observed_model(result, metrics, verify_payload, probe_info, text)
    observed = {
        "model": observed_model,
        "image": _observed_image(result, metrics, probe_info),
        "artifact_contract": build_manifest(path) if path.exists() else {"files": [], "file_count": 0, "total_bytes": 0},
        "hardware": _hardware_summary(metrics, probe_info),
        "gpu_utilization_evidence": collect_hardware_utilization_evidence(path / "metrics.json"),
        "cache": _cache_summary(result, metrics, probe_info, text),
        "workspace_contract": _workspace_contract_summary(result, metrics, probe_info, submit_result, text, spec),
        "http_statuses": [int(item) for item in HTTP_STATUS_RE.findall(text)],
    }
    observed["workspace_observation_coverage"] = workspace_observation_coverage(provider, observed, artifact_verify)
    checks = _checks(spec, observed, artifact_verify, result, verify_payload)
    failure = _failure(provider, spec, checks, observed, text, artifact_verify)
    ok = all(bool(value) for value in checks.values())
    module_probe_name = provider_module_probe_name(_probe_name(spec, probe_name), spec)
    module_canary_evidence = provider_module_canary_evidence(
        module_id=str(spec.get("provider_module_id") or ""),
        parent_provider=provider,
        provider_module_probe_name=module_probe_name,
        workspace_observation_coverage=observed["workspace_observation_coverage"],
    )
    record = {
        "contract_probe_version": CONTRACT_PROBE_VERSION,
        "provider": provider,
        "probe_name": _probe_name(spec, probe_name),
        "provider_module_probe_name": module_probe_name,
```

- `src/gpu_job/provider_contract_probe.py`
  - `_canary_job()` が 5 系統の job 初期化を 1 関数で抱えています。`runpod` の `serverless_handler` / `pod_http`、`vast` の `serverless_pyworker` / `direct instance`、`modal` の特殊化が全てここに入っています。分岐密度が高すぎます。
- `src/gpu_job/providers/runpod.py`
  - `submit()` は `smoke`, `asr_diarization`, `llm_heavy + pod_http`, `llm_heavy + endpoint` を 1 メソッドで切り替えています。
  - `submit()` の endpoint 系と `_submit_pod_worker()` の pod 系で、`result.json`, `metrics.json`, `probe_info.json`, `stdout.log`, `stderr.log`, `verify.json` を書く処理が重複しています。
  - `_run_llm_endpoint()` は polling ロジックを巨大な Python ワンライナー文字列として内包しています。読みやすさが悪く、修正点の局所化ができていません。
- `src/gpu_job/providers/vast.py`
  - `submit()` の smoke path と `_submit_direct_instance_asr()` で、phase 遷移、artifact 出力、cleanup、verify が重複しています。
  - `_submit_direct_instance_asr()` は preflight、image contract 検証、secret 検証、offer 選定、startup retry、SSH/SCP 実行、artifact 収集、cleanup を全部抱えています。
- `src/gpu_job/workspace_registry.py`
  - `provider_workspace_plan()` が `provider_module_contract` を付与し、`record_workspace_state()` でも同じ契約情報を再投影しています。記録構造の組立が分散しています。
- `src/gpu_job/execution_record.py`
  - `workspace_plan`, `plan_quote`, `provider_module_contract` の再構成が `workspace_registry.py` と役割重複です。共通 serializer を持つべきです。
- `src/gpu_job/launch_gate.py` と `docs/launch-slice-manifest.json`
  - gate 側は free-text の `review_status` / `blocks` 文字列に依存しています。文書の文言と判定ロジックが暗黙結合しています。コード削減対象というより、可読性阻害要因です。
- `tests/test_runpod_serverless_probe_script.py`, `tests/test_vast_serverless_probe_script.py`
  - script をファイルパス直指定で import しています。script と adapter の統合は簡素化に見えても、現在はテスト結合があるため即時削減対象ではありません。

## 3. 絶対に触ってはいけない部分
- `routing_by_module_enabled` は未使用フラグではなく reject 条件です。`false` 固定を維持しない限り gate と policy test が壊れます。

```94:107:src/gpu_job/policy_engine.py
def _validate_provider_module_routing(policy: dict[str, Any]) -> list[str]:
    if "provider_module_routing" not in policy:
        return []
    routing = policy.get("provider_module_routing")
    if not isinstance(routing, dict):
        return ["provider_module_routing must be an object"]
    errors = []
    enabled = routing.get("routing_by_module_enabled", False)
    if enabled is not False:
        errors.append("provider_module_routing.routing_by_module_enabled must remain false until module routing is implemented")
```

- `provider_module_contract` は routing key ではなく可視化 metadata です。`provider_module_contract_for_job()` の `selection.routing_by_module_enabled=False` と、`_workspace_hash()` の `stable.pop("provider_module_contract", None)` は不可侵です。

```431:444:src/gpu_job/provider_module_contracts.py
    return {
        "provider_module_contract_version": PROVIDER_MODULE_CONTRACT_VERSION,
        "parent_provider": parent_provider,
        "active_module_id": active,
        "requested_module_id": requested,
        "available_module_ids": available_ids,
        "available_modules": modules,
        "active_module": active_contract,
        "selection": {
            "mode": "metadata_requested_or_parent_default",
            "routing_by_module_enabled": False,
            "requested_module_valid": bool(requested and requested in available_ids),
            "reason": ("provider module is recorded for contract visibility; execution still uses the parent provider adapter"),
        },
    }
```

```270:275:src/gpu_job/workspace_registry.py
def _workspace_hash(plan: dict[str, Any]) -> str:
    stable = dict(plan)
    stable.pop("created_at", None)
    stable.pop("workspace_plan_id", None)
    stable.pop("provider_module_contract", None)
```

- serverless identity evidence の厳格条件は不可侵です。`runpod_serverless` は `endpoint_id` 必須、`vast_pyworker_serverless` は `endpoint_id` と `workergroup_id` 必須です。`tests/test_provider_module_contracts.py` も固定しています。
- `provider_adapter_diff_empty` は launch stop condition です。`src/gpu_job/providers/*` への変更を含むリファクタリングは、今回の条件では実施禁止です。

```25:37:src/gpu_job/launch_gate.py
    provider_adapter_diff = _git_diff_names(["src/gpu_job/providers"])

    policy_validation = validate_policy(policy)
    routing_flag = dict(policy.get("provider_module_routing") or {})
    routing_disabled = routing_flag.get("routing_by_module_enabled") is False
    rejects_true = _routing_true_is_rejected(policy)
    phase0 = _phase(
        "phase_0_current_diff_fixed",
        [
            ("policy_valid", bool(policy_validation.get("ok"))),
            ("routing_by_module_enabled_false", routing_disabled),
            ("routing_by_module_true_rejected", rejects_true),
            ("provider_adapter_diff_empty", not provider_adapter_diff),
```

- `RunPodProvider.plan_asr_endpoint()` の `workersMin=0`, `workersStandby=0`, `workersMax=1`, `production_dispatch="blocked_until_serverless_handler_and_workspace_contract_probe_pass"` は不可侵です。`tests/test_runpod_serverless_asr.py` が固定しています。

```1018:1028:src/gpu_job/providers/runpod.py
            "safety_invariants": {
                "workers_min": 0,
                "workers_standby": 0,
                "workers_max": workers_max,
                "idle_timeout_seconds": idle_timeout,
                "requires_clean_pre_guard": True,
                "requires_clean_post_guard": True,
                "requires_hf_secret_ref": bool(hf_secret_name),
                "requires_workspace_observation_parity": True,
                "production_dispatch": "blocked_until_serverless_handler_and_workspace_contract_probe_pass",
            },
```

- Modal は 1 probe に畳めません。`launch_gate.py` は `modal.llm_heavy.qwen2_5_32b` と `modal.asr_diarization.pyannote` の両方を別条件として要求しています。
- `scripts/runpod-asr-serverless-contract-probe.py` と `scripts/vast-pyworker-serverless-contract-probe.py` は現時点で独立維持です。script 直 import test が存在します。

## 4. リファクタリング候補を優先順位付きで列挙
- `P0`: `src/gpu_job/provider_contract_probe.py` を内部モジュールへ分割し、公開 API 名は維持する。対象は `DEFAULT_CONTRACT_PROBES`, `parse_contract_probe_artifact()`, `_canary_job()`, `_observed_*`, `_checks()`, `_failure()`。
- `P0`: `DEFAULT_CONTRACT_PROBES` の重複リテラルを builder 化する。probe 名は保持し、`required_files`, `expected_image_digest`, `forbidden_models`, `cache_required` の手書きを削る。
- `P0`: `workspace_registry.py` と `execution_record.py` の契約投影処理を共通 helper 化する。`provider_module_contract` と `plan_quote.selected_option` の整形を 1 箇所に集約する。
- `P1`: `launch_gate.py` の manifest 依存文字列を定数化し、`docs/launch-slice-manifest.json` との coupling を明示化する。挙動は一切変えない。
- `P1`: artifact 書込の共通 helper を新設する。ただし `src/gpu_job/providers/*` を触らない範囲に限定し、まず `provider_contract_probe.py` と記録層のみで使う。
- `P2` 重大だが現条件では着手禁止: `src/gpu_job/providers/runpod.py` を `pod`, `serverless`, `artifacts`, `llm_endpoint` に分割する。
- `P2` 重大だが現条件では着手禁止: `src/gpu_job/providers/vast.py` を `direct_instance_smoke`, `direct_instance_asr`, `serverless_contract`, `cleanup/lifecycle` に分割する。

## 5. 最終的な理想形のモジュール構成
```text
src/gpu_job/
  contract_probe/
    api.py
    registry.py
    spec_builder.py
    artifact_reader.py
    observation.py
    checks.py
    failure.py
    canary_job.py
  records/
    workspace_plan.py
    execution_record.py
    artifact_bundle.py
  provider_contracts/
    module_contracts.py
    image_contracts.py
    workspace_observation.py
  providers/                  # これは別タスクでのみ変更
    runpod/
      api_client.py
      pod_worker.py
      serverless_endpoint.py
      artifacts.py
    vast/
      direct_instance.py
      serverless_template.py
      lifecycle.py
      artifacts.py
```

現条件下の実装形は、外向きには既存の `src/gpu_job/provider_contract_probe.py`, `src/gpu_job/workspace_registry.py`, `src/gpu_job/execution_record.py` を facade として残し、内部だけ分割する形が最適です。

## 6. その理想形へ至る段階的リファクタリング計画
1. `Phase 0`: 不可侵契約を固定する。`routing_by_module_enabled=false`, `provider_adapter_diff_empty`, `workspace_plan_id` 安定性、serverless identity evidence、RunPod scale-to-zero invariant を回帰テストで凍結する。
2. `Phase 1`: `src/gpu_job/provider_contract_probe.py` から pure helper を分離する。最初に `_read_json`, `_artifact_text`, `_observed_*`, `_hardware_summary`, `_cache_summary` を移す。公開関数名は維持する。
3. `Phase 2`: `parse_contract_probe_artifact()` を `read -> observe -> check -> classify -> record` の 5 段に割る。返却 JSON shape は 1 byte も変えない前提で進める。
4. `Phase 3`: `DEFAULT_CONTRACT_PROBES` を builder 化する。probe 名、`provider_module_probe_name`、required categories は完全固定で、重複リテラルだけ削る。
5. `Phase 4`: `workspace_registry.py` と `execution_record.py` の共通 projection helper を導入する。`provider_module_contract` と `plan_quote.selected_option` の組立を 1 箇所へ寄せる。
6. `Phase 5`: `launch_gate.py` の manifest 依存を定数化する。`docs/launch-slice-manifest.json` の wording は変えず、コード側の意味を明示する。
7. `Phase 6`: 別タスクとして条件解除後に `src/gpu_job/providers/runpod.py` と `src/gpu_job/providers/vast.py` を分割する。この段階で初めて provider adapter diff が発生する。

## 7. リスクと回帰試験計画
- 最大リスクは probe 名変更です。`probe_name` と `provider_module_probe_name` が変わると `launch_gate.py`, `tests/test_provider_module_contracts.py`, `tests/test_launch_gate.py` が同時に壊れます。
- 次のリスクは `workspace_plan_id` 変化です。`provider_module_contract` を hash 対象に入れる変更は不可です。
- 次のリスクは serverless identity evidence の緩和です。`endpoint_id` / `workergroup_id` 必須条件を弱めると、green は見かけ上維持できても launch gate の意味が壊れます。
- 次のリスクは script の場所変更です。`tests/test_runpod_serverless_probe_script.py` と `tests/test_vast_serverless_probe_script.py` はファイルパス import に依存しています。
- 次のリスクは docs wording 変更です。`launch_gate.py` は `docs/launch-slice-manifest.json` の文字列を判定に使っています。

回帰試験は `docs/launch-slice-manifest.json` にある既定セットをそのまま使うのが最短です。Ask mode のため未実行ですが、実施順はこれで十分です。

```bash
uv run python -m pytest tests/test_provider_contract_probe.py tests/test_provider_module_contracts.py tests/test_launch_gate.py tests/test_policy_router.py -q
uv run python -m pytest tests/test_runpod_config.py tests/test_runpod_serverless_asr.py tests/test_vast_asr_provider.py tests/test_modal_provider.py tests/test_modal_llm_quality.py -q
uv run python -m pytest tests/test_provider_catalog_contracts.py tests/test_asr_diarization_contract.py tests/test_image_distribution.py -q
uv run python -m pytest -q
uv run gpu-job selftest
uv run gpu-job validate examples/jobs/asr.example.json
```

## 8. 具体的にどの関数/分岐/データ構造を縮約・統合・削除するか
- `src/gpu_job/provider_contract_probe.py`
  - 縮約: `DEFAULT_CONTRACT_PROBES` の各 entry に繰り返し出る `required_files`, `expected_image_digest`, `forbidden_models`, `cache_required`。
  - 統合: `_read_json`, `_artifact_text`, `_observed_model`, `_observed_image`, `_hardware_summary`, `_cache_summary`, `_workspace_contract_summary` を `artifact_reader` / `observation` 層へ統合。
  - 分離: `parse_contract_probe_artifact()` を `read_artifact_bundle()`, `build_observed_contract()`, `evaluate_probe_checks()`, `classify_probe_failure()`, `build_probe_record()` 相当に分ける。
  - 分離: `_canary_job()` を `modal_canary_job()`, `runpod_canary_job()`, `vast_canary_job()` の builder 群へ分ける。
- `src/gpu_job/workspace_registry.py` と `src/gpu_job/execution_record.py`
  - 統合: `provider_module_contract` の可視化投影と `plan_quote` への再投影。
  - 削除候補: 同一意味の inline dict 組立コード。
- `src/gpu_job/launch_gate.py`
  - 統合: `_slice_status()` / `_slice_blocks()` が参照する magic string 群。
  - 削除候補: free-text 直書きの重複比較。
- `src/gpu_job/providers/runpod.py`
  - 将来分離対象: `submit()`, `_submit_pod_worker()`, `_run_llm_endpoint()`, `plan_asr_endpoint()`, `plan_vllm_endpoint()`。
  - 今回は削除・統合しない。理由は `provider_adapter_diff_empty` 制約です。
- `src/gpu_job/providers/vast.py`
  - 将来分離対象: `submit()`, `_submit_direct_instance_asr()`, `destroy_instance()`, `recommended_templates()`。
  - 今回は削除・統合しない。理由は `provider_adapter_diff_empty` 制約です。

実際に着手するなら、最初の PR は `src/gpu_job/provider_contract_probe.py` の内部分割だけに限定するのが正解です。次の自然な一手は、その PR 用に「公開 API 不変」「probe 名不変」「workspace_plan_id 不変」の 3 条件を先にチェックリスト化することです。
