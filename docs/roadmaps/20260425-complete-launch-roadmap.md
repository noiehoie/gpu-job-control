# 完全ローンチ・ロードマップ

前提事実:
- `launch_phase_gate()` の現在値は `overall_ok=true`
- `routing_by_module_enabled=false`
- `provider_adapter_diff=[]`
- `phase_0`〜`phase_5` はすべて `ok=true`
- `stop_conditions=[]`
- `contract_probe_summary.count=48`
- 5系統の最新 probe は揃っている
- ただし `launch_gate.py` のトップレベル `ok` は `phase_0`〜`phase_2` の論理積であり、**完全ローンチの十分条件ではない**

## 完全ローンチの定義

次の 5 条件を、**netcup 上で同一コミットに対して**満たした状態を完全ローンチとする。

1. `readiness --phase-report` の `phases` 配列で `phase_0`〜`phase_5` が全て `ok=true`
2. `stop_conditions=[]`
3. `provider_adapter_diff=[]` かつ `routing_by_module_enabled=false`
4. `pytest -q` / `gpu-job selftest` / `gpu-job validate examples/jobs/asr.example.json` が全て exit 0
5. 5系統の運用役割を `docs/launch-decision.md` に固定し、最終 manifest / decision / gate 文書が最新ログで更新されている

## 現在地

| 系統 | 現在地 | ローンチに必要な残作業 |
| --- | --- | --- |
| `modal` | phase_3 green。`modal.llm_heavy.qwen2_5_32b` と `modal.asr_diarization.pyannote` がある | netcup で 1 回再現し、production primary として運用宣言に反映 |
| `runpod pod` | phase_4 green。bounded pod canary 証跡あり | create → health/generate → verify → terminate → post-guard を netcup で再固定 |
| `runpod serverless` | phase_4 green。`serverless_handler` / `official_whisper_smoke` / `heartbeat` がある | 承認済み `endpoint_id` を固定し、その endpoint の identity evidence を再固定 |
| `vast direct instance` | phase_5 green。direct instance canary 証跡あり | reserve 専用として image/digest・cleanup・residue-zero を最終文書へ固定 |
| `vast serverless` | phase_5 green。`vast.asr.serverless_template` がある | `endpoint_id` + `workergroup_id` を持つ pyworker evidence を 1 件 netcup で再固定 |

## Phase R0 — netcup 再現固定

- 目的:
  - Mac Studio 非依存で、同一コミットの gate と CI を netcup で再現する
- 実作業:
  - netcup 上で対象コミット checkout
  - `uv sync --extra providers`
  - `uv run gpu-job-admin readiness --phase-report --limit 100`
  - `uv run python -m pytest -q`
  - `uv run gpu-job selftest`
  - `uv run gpu-job validate examples/jobs/asr.example.json`
- Gate:
  - `phases[*].ok=true` が 6 件
  - `stop_conditions=[]`
  - `provider_adapter_diff=[]`
  - `routing_by_module_enabled=false`
  - 上記 3 コマンドが全て exit 0
- Stop:
  - `billing_guard_failed`
  - `provider_adapter_diff_present`
  - `routing_by_module_enabled_not_false`
- 成果物:
  - netcup 実行ログ
  - `readiness-R0.json`

## Phase R1 — identity 固定

- 目的:
  - Serverless 2系統の identity evidence を「運用対象」として固定する
- 実作業:
  - `runpod_serverless` の承認済み `endpoint_id` を設定ファイルへ記録
  - その endpoint で `official_whisper_smoke` か `serverless_handler` を再実行
  - `vast_pyworker_serverless` の `endpoint_id` と `workergroup_id` を持つ pyworker canary を netcup で再実行
- Gate:
  - RunPod: `endpoint_id` を持つ最新 artifact
  - Vast: `endpoint_id` と `workergroup_id` を持つ最新 artifact
  - いずれも `provider_module_canary_evidence.ok=true`
- Stop:
  - identity 欠落
  - cleanup 後に residue が残る
  - guard が dirty
- 成果物:
  - fixed serverless identity artifact
  - updated canary evidence

## Phase R2 — role freeze

- 目的:
  - 5系統の役割を「運用上の真実」として固定する
- 実作業:
  - `docs/launch-decision.md` を最新ログで更新
  - `docs/launch-phase0-5-gate.md` に netcup 実行結果を反映
  - `docs/launch-slice-manifest.json` を最新 state で見直し
- Gate:
  - `modal` は `production_primary`
  - `runpod_pod` は `conditional batch`
  - `runpod_serverless` は承認済み endpoint 限定
  - `vast_instance` と `vast_pyworker_serverless` は `reserve/canary`
- Stop:
  - Vast を `production_primary` に昇格しようとする変更
  - RunPod Serverless vLLM / Hub-template を launch blocker に戻す変更
- 成果物:
  - frozen launch decision
  - updated slice manifest

## Phase R3 — repeat canary

- 目的:
  - single-success ではなく repeat-success にする
- 実作業:
  - 5系統を netcup でもう一周回す
  - artifact / guard / cleanup / identity を再確認
- Gate:
  - 5系統すべて latest success
  - `guard_summary.providers.*.billable_count == 0` で終了
- Stop:
  - hidden warm capacity
  - orphan resource
  - cleanup failure
- 成果物:
  - repeat canary evidence bundle

## Phase R4 — launch freeze

- 目的:
  - 完全ローンチ状態を git と文書で凍結する
- 実作業:
  - 最終 commit を切る
  - tag を付ける
  - launch docs を freeze
- Gate:
  - `git status --short` が clean
  - final gate / ci / canary logs が repo に揃っている
- Stop:
  - freeze 直前に provider adapter 差分が出る
  - `routing_by_module_enabled=true` が混入する
- 成果物:
  - launch tag
  - frozen launch docs

## 直近 48 時間の順序

1. R0 を先に完了する
2. R1 で serverless identity を固定する
3. R2 で role freeze を文書に反映する
4. R3 で 5系統 repeat canary を回す
5. R4 で git/tag/docs を凍結する

## 絶対にやらないこと

1. Mac Studio で Docker build/push
2. `routing_by_module_enabled=true`
3. provider adapter の場当たり修正
4. Vast の `production_primary` 昇格
5. serverless identity evidence なしの promotion
6. fresh provider read と preflight なしの destructive cleanup
