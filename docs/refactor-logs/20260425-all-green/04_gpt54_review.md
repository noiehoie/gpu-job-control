## 確認事実
- `rg "plan_quote|selected_option" src/gpu_job/workspace_registry.py` の出力は `No matches found`。`src/gpu_job/workspace_registry.py` は今回の `plan_quote` 共通化対象ではない。
- `rg "build_plan_quote\(|def _plan_quote_from_job\(" src/gpu_job/execution_record.py` の出力は `_plan_quote_from_job` と `return build_plan_quote(...)`。`src/gpu_job/execution_record.py` はすでに `build_plan_quote()` に収束している。
- `rg "build_plan_quote\(|plan\\[\"plan_quote\"\\]" src/gpu_job/contracts.py` の出力は `quote = build_plan_quote(plan)` と `plan["plan_quote"] = quote`。`src/gpu_job/contracts.py` もすでに共通化済み。
- `rg "DEFAULT_CONTRACT_PROBES|_canary_job" tests/test_provider_contract_probe.py` の出力は、`tests/test_provider_contract_probe.py` が `DEFAULT_CONTRACT_PROBES` と `_canary_job` を直接参照していることを示す。新規モジュール分割は import 面の回帰点を増やす。

## 再審査結果
本当に今やるべき最小安全集合は、`src/gpu_job/provider_contract_probe.py` の `DEFAULT_CONTRACT_PROBES` の静的重複だけを減らす 1 件です。  
これを安全にするため、先に `tests/test_provider_contract_probe.py` に回帰固定テストを追加します。  
それ以外は今回すべて削除または延期します。

削除・延期の判定は以下です。

- 削除: `src/gpu_job/provider_contract_probe.py::parse_contract_probe_artifact()` の段階分割  
  理由: 現在でも関数境界は十分小さい。新しい内部 API を増やすだけで、green 維持には直結しない。
- 削除: `src/gpu_job/provider_contract_probe.py::_workspace_contract_summary()` の表駆動化  
  理由: `result` / `metrics` / `probe_info` / `submit_result` の優先順位と `None` / `False` の扱いが密結合。ここを表にすると壊しやすい。
- 削除: `src/gpu_job/provider_contract_probe.py::workspace_observation_coverage()` のループ化  
  理由: すでに `_coverage_entry()` で十分整理済み。12カテゴリを無理に表にすると可読性の利益が小さい。
- 削除: `src/gpu_job/provider_contract_probe.py::_canary_job()` の provider 別分割  
  理由: RunPod/Vast の cost・metadata・identity 条件が多く、純粋な見た目改善のために触る範囲が広すぎる。
- 削除: `src/gpu_job/execution_record.py::_plan_quote_from_job()` と `src/gpu_job/contracts.py` / `src/gpu_job/requirements.py` の DRY 化  
  理由: 事実として `workspace_registry.py` は無関係で、`contracts.py` と `execution_record.py` はすでに `build_plan_quote()` を使っている。`src/gpu_job/requirements.py` は `plan_quote` を組み立てていない。
- 削除: `src/gpu_job/launch_gate.py::_slice_status()` / `_slice_blocks()` 周辺の定数化  
  理由: 1 行関数の周辺で、回帰防止効果がない。別ファイルの安定ゲートを無意味に触るだけ。
- 維持: `src/gpu_job/providers/*` 非接触、`routing_by_module_enabled=False` 維持、`docs/launch-slice-manifest.json` 非変更  
  理由: ここは現計画の安全条件として正しい。

## 最も安全な実装順
1. 非コード前提  
   `AGENTS.md` に従って council の `research` と `design` を先に記録する。続いて `uv run python -m pytest -q` と `uv run gpu-job selftest` をベースラインとして固定する。ここではソースを変えない。

2. `tests/test_provider_contract_probe.py` を先に更新  
   追加するのは 1 テストだけでよい。`DEFAULT_CONTRACT_PROBES` の全エントリについて、`_checks()` と `_canary_job()` が読むキー群が欠落していないことを固定する。  
   固定対象キー: `provider`, `provider_module_id`, `workload_family`, `job_type`, `gpu_profile`, `expected_model`, `expected_image`, `expected_image_digest`, `forbidden_models`, `required_files`, `require_gpu_utilization`, `cache_required`。  
   条件付きキー: `workspace_contract_required`, `image_contract_id`, `serverless_handler_contract_required`, `official_template_smoke_required`。

3. `src/gpu_job/provider_contract_probe.py` だけを変更  
   `DEFAULT_CONTRACT_PROBES` に対してのみ、静的な `dict` アンパックで重複を減らす。  
   許容する抽象化は同一ファイル内の base dict まで。  
   禁止する抽象化は builder、loop 生成、class 化、新規モジュール分割。  
   変更対象は `DEFAULT_CONTRACT_PROBES` の定義だけ。`parse_contract_probe_artifact()`、`_workspace_contract_summary()`、`workspace_observation_coverage()`、`_canary_job()` は触らない。

4. 検証して終了  
   まず `uv run python -m pytest -q tests/test_provider_contract_probe.py`、次に `uv run python -m pytest -q tests/test_provider_module_contracts.py tests/test_launch_gate.py`、最後に全量 `uv run python -m pytest -q`。必要なら `uv run gpu-job selftest` も再実行する。  
   ここで green ならこのラウンドは終了。次ラウンドに進まない。

## 実装対象ファイル
- `tests/test_provider_contract_probe.py`  
  `DEFAULT_CONTRACT_PROBES` のキー欠落と静的 shape を固定する回帰テストを 1 件追加。
- `src/gpu_job/provider_contract_probe.py`  
  `DEFAULT_CONTRACT_PROBES` のみ静的重複削減。`dict` アンパックだけを使う。関数分割なし、新規ファイル追加なし。

## 今回の対象外ファイル
- `src/gpu_job/execution_record.py`
- `src/gpu_job/contracts.py`
- `src/gpu_job/requirements.py`
- `src/gpu_job/workspace_registry.py`
- `src/gpu_job/launch_gate.py`
- `src/gpu_job/providers/*`
- `docs/launch-slice-manifest.json`

## 採用すべき最終計画案
- council の `research` / `design` を記録し、`uv run python -m pytest -q` と `uv run gpu-job selftest` でベースラインを固定する。
- `tests/test_provider_contract_probe.py` に、`DEFAULT_CONTRACT_PROBES` の必須キー固定テストを追加する。
- `src/gpu_job/provider_contract_probe.py` の `DEFAULT_CONTRACT_PROBES` だけを、同一ファイル内の静的 base dict による `**` 展開へ置き換える。
- `parse_contract_probe_artifact()`、`_workspace_contract_summary()`、`workspace_observation_coverage()`、`_canary_job()`、`_plan_quote_from_job()`、`_slice_status()`、`_slice_blocks()` は今回変更しない。
- 検証は `tests/test_provider_contract_probe.py` → `tests/test_provider_module_contracts.py tests/test_launch_gate.py` → 全量 `pytest` の順で実行し、green ならこのラウンドを終了する。
