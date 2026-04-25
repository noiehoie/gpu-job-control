以下に、両文書の精読結果と Gemini 監査による訂正を反映した「リファクタリング基本計画」を実装順でまとめます。

---

## 訂正サマリ（Gemini 指摘の反映）

1. **ファイル役割の誤認訂正**  
   `01_gpt54_report.md` は `src/gpu_job/workspace_registry.py` と `src/gpu_job/execution_record.py` を「`plan_quote.selected_option` の組立が重複する対」として扱っているが、**`workspace_registry.py` に `plan_quote` / `selected_option` の再構築は存在しない**（当リポジトリで `rg` により一致ゼロを確認）。  
   **実際の重複軸**は `execution_record.py` の `_plan_quote_from_job()` と、プランニング層（`src/gpu_job/contracts.py`、`src/gpu_job/requirements.py` 等）側の表現である。

2. **`DEFAULT_CONTRACT_PROBES` の「builder 化」方針の却下**  
   `DEFAULT_CONTRACT_PROBES` は `tests/test_provider_contract_probe.py` が `DEFAULT_CONTRACT_PROBES["runpod.asr_diarization.pyannote"]` 等で**直接参照する宣言的リテラル**である。ファクトリ／動的生成型の builder は静的検証を弱め、キー欠落で green が割れるリスクが高い。**共通化は `**DEFAULT_BASE_PROBE` 等の辞書アンパックによるリテラルベースの重複削減に限定する。**

3. **見落としの追加**  
   `provider_contract_probe.py` 内の **`_workspace_contract_summary`（約149行）** 周辺で、`result` / `metrics` / `probe_info` に対する `_first_bool`、`_first_string`、`_nested_first`、`_nested_number` の呼び出しが冗長に並ぶ点、および **`workspace_observation_coverage` 内の `_coverage_entry` が12カテゴリ分ハードコード**されている点を、別ファイルへ移すだけでなく**定義ベースの抽出・カバレッジ表**への整理として計画に組み込む。

---

## 不変条件（全ステップ共通）

- **`routing_by_module_enabled` は `False` のまま**（`policy_engine.py` の `_validate_provider_module_routing()`、`launch_gate.py` の phase 0 条件）。ポリシー JSON の意味変更は行わない。
- **`src/gpu_job/providers/*` は変更しない**（`launch_gate.py` の `provider_adapter_diff_empty`＝`src/gpu_job/providers` に対する git diff 空を維持）。
- **`provider_module_contract` の意味と `_workspace_hash()` からの除外**（`workspace_registry.py`）は維持。
- **serverless identity**（`runpod_serverless` の `endpoint_id`、`vast_pyworker_serverless` の `endpoint_id` / `workergroup_id`）の厳格条件は緩めない。
- **probe 名・`provider_module_probe_name`・返却 JSON の shape**はバイト単位で変えない前提（分割・共通化は内部実装のみ）。

---

## 実装順リファクタリング基本計画

### ステップ 0: 回帰ベースラインの固定（コード変更なしまたはチェックリストのみ）

| 項目 | 内容 |
|------|------|
| **何を変えるか** | 原則なし。着手前に「現在 all green」の事実をコマンドで固定する運用（記録のみ可）。 |
| **何を変えないか** | 全ソース、特に `providers/`、`config/` のポリシー意味、`docs/launch-slice-manifest.json` の文言。 |
| **なぜ安全か** | 変更がないため後続の差分の原因にならない。 |
| **どのテストで守るか** | `uv run python -m pytest -q` および `uv run gpu-job selftest`（`01_gpt54_report.md` セクション7のコマンド群をベースラインとして実行）。 |

---

### ステップ 1: `provider_contract_probe.py` から純粋ヘルパの内部モジュール分離（挙動同一）

| 項目 | 内容 |
|------|------|
| **何を変えるか** | `_read_json`、`_artifact_text`、`_observed_model`、`_observed_image`、`_hardware_summary`、`_cache_summary` 等、副作用が artifact 読取に限定される関数を **`src/gpu_job/` 配下の新規内部モジュール**（例: `provider_contract_probe_artifact.py` 等、実装時に命名）へ移し、`provider_contract_probe.py` は再エクスポートまたは薄い呼び出しにする。 |
| **何を変えないか** | 公開シンボル（例: `parse_contract_probe_artifact`、`DEFAULT_CONTRACT_PROBES`、`_canary_job` の外部からの import 経路がテストに依存する場合は互換維持）、各関数の入出力、probe 名キー。 |
| **なぜ安全か** | 純関数移動のみで契約テストが検証する観測値が変わらない。 |
| **どのテストで守るか** | `tests/test_provider_contract_probe.py`、`tests/test_provider_module_contracts.py`、`tests/test_launch_gate.py`（probe 連鎖）。 |

---

### ステップ 2: `parse_contract_probe_artifact()` の内部段階分割（返却 dict の shape 不変）

| 項目 | 内容 |
|------|------|
| **何を変えるか** | `parse_contract_probe_artifact()`（`provider_contract_probe.py` 352行付近開始）を、**read → observe → check → classify → record** の内部関数または内部モジュール関数に分割。呼び出し順とキー集合は現状と同一。 |
| **何を変えないか** | `verify_artifacts()`、`contract_probe_spec()`、`provider_module_canary_evidence()`、`provider_module_probe_name()` 等の**意味**；`CONTRACT_PROBE_VERSION` を含む record フィールド集合。 |
| **なぜ安全か** | 制御フローのみの分解で、観測可能な戻り値を変えない設計にすれば契約は維持される。 |
| **どのテストで守るか** | `tests/test_provider_contract_probe.py`（artifact パースの網羅）、`tests/test_launch_gate.py`（記録・ゲート連携）。 |

---

### ステップ 3: `DEFAULT_CONTRACT_PROBES` のリテラル重複削減（`**` 共通辞書のみ）

| 項目 | 内容 |
|------|------|
| **何を変えるか** | 各 probe エントリに繰り返される `required_files`、`expected_image_digest`、`forbidden_models`、`cache_required` 等を、**共通の基底 dict**（例: `DEFAULT_BASE_PROBE`）を定義し、各エントリは `{**DEFAULT_BASE_PROBE, "probe_name": ..., ...}` の形で記述。**キーは欠落させない。** |
| **何を変えないか** | マージ後の**各キーの最終値**（特に probe 固有情報）；クラスベース builder・ループ生成による動的キー集合。 |
| **なぜ安全か** | テストが参照する `DEFAULT_CONTRACT_PROBES["..."]` の辞書内容が静的に読み取れるままであるため、Gemini が指摘した「動的 builder」のリスクを避ける。 |
| **どのテストで守るか** | `tests/test_provider_contract_probe.py`（`DEFAULT_CONTRACT_PROBES` 直接参照、`_canary_job(DEFAULT_CONTRACT_PROBES[...])`）。 |

---

### ステップ 4: 多辞書フォールバック抽出と `workspace_observation_coverage` の表駆動化

| 項目 | 内容 |
|------|------|
| **何を変えるか** | `_workspace_contract_summary()` 内の `_first_bool` / `_first_string` / `_nested_first` / `_nested_number` の反復を、**スキーマまたはテーブル定義**から同じ抽出順序で実行する実装に置き換え。`workspace_observation_coverage()` 内の `_coverage_entry(...)` 12カテゴリのハードコードを、**カテゴリ名と引数のリストの反復**に集約（出力キー名は現状維持）。 |
| **何を変えないか** | 抽出優先順序（どの dict のどのキーを先に見るか）、`observed` dict のキー名、`workspace_observation_coverage` の戻り値構造。 |
| **なぜ安全か** | リファクタは「同じ入力に対する同じ出力」の機械的整理であり、ゲートが依存する coverage 意味を変えない前提で進める。 |
| **どのテストで守るか** | `tests/test_provider_contract_probe.py`、`tests/test_provider_module_contracts.py`（workspace / module evidence）。 |

---

### ステップ 5: `_canary_job()` の provider 別分岐の薄い分割（`providers/` 非接触）

| 項目 | 内容 |
|------|------|
| **何を変えるか** | `_canary_job()` が抱える `runpod`（`serverless_handler` / `pod_http`）、`vast`（`serverless_pyworker` / direct instance）、`modal` の分岐を、**同一ファイル内の名前付き関数**またはサブモジュール（例: `_canary_job_runpod`）に切り出す。 |
| **何を変えないか** | 生成する job dict の構造、環境変数・metadata の意味、`src/gpu_job/providers/` への import 以外の依存関係の意味。 |
| **なぜ安全か** | adapter を変更せず、canary job 組立のみの可読化。 |
| **どのテストで守るか** | `tests/test_provider_contract_probe.py`（`_canary_job` と `DEFAULT_CONTRACT_PROBES` の組み合わせ）。 |

---

### ステップ 6: `plan_quote` 投影の DRY 化（対象は `execution_record` とプランニング層／`workspace_registry` は主対象外）

| 項目 | 内容 |
|------|------|
| **何を変えるか** | **`src/gpu_job/execution_record.py` の `_plan_quote_from_job()`** と **`src/gpu_job/contracts.py` / `src/gpu_job/requirements.py`**（および重複があれば近傍）の間で、**`plan_quote` / `selected_option` の組立ロジック**を共通ヘルパー（新規小モジュールまたは既存のどちらか一方への集約）に寄せる。 |
| **何を変えないか** | **`src/gpu_job/workspace_registry.py` の責務**（`provider_workspace_plan()` が構築する `workspace_plan` の形、`record_workspace_state()` の記録方針、`_workspace_hash()`）。`plan_quote` はここに存在しないため **本ステップの「plan_quote 共通化」の主戦場に含めない**。`provider_module_contract_for_job()` の単一ソース性も維持。 |
| **なぜ安全か** | Gemini 訂正どおり、誤ったファイルに手を入れず、実際に重複している層だけを触る。 |
| **どのテストで守るか** | `tests/test_policy_router.py`、`tests/test_launch_gate.py`、実行記録の形に依存するテストがあればそれに追加で `rg plan_quote execution_record` で特定。 |

---

### ステップ 7: `launch_gate.py` の manifest 依存文字列の定数化

| 項目 | 内容 |
|------|------|
| **何を変えるか** | `_slice_status()` / `_slice_blocks()`（および同様の free-text 比較箇所）が参照する **`docs/launch-slice-manifest.json` 由来の文字列**を、`launch_gate.py` 内の**名前付き定数**に集約。比較対象の文字列値は**1文字も変えない**。 |
| **何を変えないか** | `docs/launch-slice-manifest.json` の本文、ゲートの真偽結果、phase 構成。 |
| **なぜ安全か** | 定数化は可読性と単一参照点の改善のみで、実行時に比較されるリテラル値が同一なら挙動は同一。 |
| **どのテストで守るか** | `tests/test_launch_gate.py`。 |

---

### ステップ 8（別タスク・本計画の範囲外明示）: Provider 分割

| 項目 | 内容 |
|------|------|
| **何を変えるか** | 本基本計画では**実施しない**。条件: `provider_adapter_diff_empty` が許容される別タスクで `runpod.py` / `vast.py` の分割を検討。 |
| **何を変えないか** | 現フェーズでは `src/gpu_job/providers/*` の一切。 |
| **なぜ安全か** | 触らないため launch phase 0 を壊さない。 |
| **どのテストで守るか** | 該当なし（着手しない）。 |

---

## GPT-5.4 High / Gemini / Claude 再審査用の論点一覧

1. **`DEFAULT_CONTRACT_PROBES` の共通化の上限**  
   `**` 基底辞書以外に、小さな「部分テンプレート」（例: runpod 系だけ共通）まで許容するか。テストが期待するキーの完全列挙をどうレビューで担保するか。

2. **`parse_contract_probe_artifact()` 分割の粒度**  
   5段（read/observe/check/classify/record）で十分か。`failure` 分類と `module_canary_evidence` の境界をどこに置くと、将来の probe 追加時のバグが最も見えるか。

3. **フォールバック抽出の「スキーマ」表現**  
   宣言的スキーマにした場合、`result` / `metrics` / `probe_info` の優先順位をコード上でどう検証し、回帰テスト不足分をどう補うか。

4. **`_plan_quote_from_job` と `contracts.py` / `requirements.py` の境界**  
   共通ヘルパーをどのモジュールに置くか（循環 import、`Job` 型の依存方向）。`workspace_registry.py` を意図的に対象外とすることへの合意。

5. **`launch_gate.py` 定数化**  
   定数を `launch_gate.py` に置くか、manifest から機械生成するか。後者はスコープ拡大になるため、本ラウンドで前者に限定するか。

6. **`_canary_job` 分割後のテスト戦略**  
   provider ごとの関数に分けたとき、`test_provider_contract_probe.py` だけで十分か、モジュール単位の追加テストが必要か。

7. **ステップ 4 とステップ 1–2 の順序の入れ替え**  
   先に表駆動化すると diff が大きくなる。先にステップ 1–2 で構造を安定させてからステップ 4に進む現順序で合意するか。

8. **Council / AGENTS.md**  
   本リファクタを material とみなす場合、`research` / `design` / `code` / `audit` の council 記録と `validate_council_audit.py` の task-id をどう切るか。

9. **スクリプトとパス依存**  
   `scripts/runpod-asr-serverless-contract-probe.py` と `scripts/vast-pyworker-serverless-contract-probe.py` は `tests/test_runpod_serverless_probe_script.py` / `tests/test_vast_serverless_probe_script.py` がパス import に依存するため、本計画で触らない方針の再確認。

10. **全量 green の定義**  
    `uv run python -m pytest -q` に加え、`uv run gpu-job validate examples/jobs/asr.example.json` を CI 相当の必須ゲートに含めるか。

---

以上が、両文書の内容、Gemini の事実訂正・危険提案の却下、および「green 最優先・provider adapter 非接触・`routing_by_module_enabled=false` 維持」を前提にした実装順ベースの基本計画である。
