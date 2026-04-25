指定された計画（`docs/refactor-logs/20260425-all-green/03_composer2_base_plan.md`）の再審査結果を報告する。

### 削るべき提案（過剰な抽象化・無駄な分割・green破壊リスク）

以下のステップは全て却下・除外する。

- **旧ステップ2: `parse_contract_probe_artifact()` の内部段階分割**
  - **理由**: 巨大な状態（辞書）を引き回すだけの無駄な関数分割。現在の単一関数での逐次処理のほうが状態の連続性が保たれ、バグの混入リスクが低い。
- **旧ステップ3: `DEFAULT_CONTRACT_PROBES` のリテラル重複削減**
  - **理由**: 辞書アンパック（`**`）による暗黙的結合は、テストが期待するキーの可読性を下げる。キーの欠落バグ（green破壊）を招くため、明示的な宣言的リテラルを維持する。
- **旧ステップ4: 多辞書フォールバック抽出と表駆動化**
  - **理由**: `_first_bool` 等の呼び出しをスキーマ・テーブル化するのは典型的な過剰な抽象化。現状の直接呼び出しが最も確実で安全。
- **旧ステップ6: `plan_quote` 投影の DRY 化**
  - **理由**: 実行記録層（`src/gpu_job/execution_record.py`）とプランニング層（`src/gpu_job/contracts.py`）を共通ヘルパで結合すると、将来の独立した変更を阻害し予期せぬ回帰を生む（過剰なDRY原則の適用）。

---

### 再設計した実装順序と具体的変更内容

副作用が完全にゼロである「定数化」から始め、次に「ファイル内での関数抽出」、最後に「別ファイルへの純粋関数移動（import変更）」の順で実装する。これが最も安全な経路である。

#### フェーズ1: ゲート文字列リテラルの定数化（ファイル内変更のみ）
- **対象ファイル**: `src/gpu_job/launch_gate.py`
- **具体的変更内容**:
  - `_slice_status` および `_slice_blocks` 関数の判定に使用されている `"locally_verified"`, `"locally_verified_after_ci"`, `"needs_provider_slice_cross_check"` 等のハードコード文字列を、ファイル上部で `STATUS_LOCALLY_VERIFIED = "locally_verified"` などの名前付き定数として定義し、すべての箇所を置換する。

#### フェーズ2: Provider別ジョブ生成のファイル内関数分割（ファイル内変更のみ）
- **対象ファイル**: `src/gpu_job/provider_contract_probe.py`
- **具体的変更内容**:
  - `_canary_job` 関数内の `if provider == ...` の各分岐ブロックを、同一ファイル内の新規関数 `_canary_job_runpod()`, `_canary_job_vast()`, `_canary_job_modal()` に抽出する。
  - 元の `_canary_job` 関数は、引数の `provider` 値に応じてこれら3つの関数へルーティングするだけの記述とする。

#### フェーズ3: 純粋関数の別モジュールへの分離（ファイル間移動）
- **対象ファイル**: `src/gpu_job/provider_contract_probe.py`、および新規作成する `src/gpu_job/provider_contract_probe_helpers.py`
- **具体的変更内容**:
  - 副作用を持たない純粋関数群（`_read_json`, `_artifact_text`, `_first_string`, `_nested_first`, `_nested_number`, `_nested_dict`, `_nested_list`, `_nested_bool`, `_first_bool` 等）を新規ファイルにカット＆ペーストで移動する。
  - `src/gpu_job/provider_contract_probe.py` に `from .provider_contract_probe_helpers import ...` の import 文を追加し、既存の呼び出し元を維持する。

---

### 採用すべき最終計画案

*   **フェーズ1（定数化）**
    *   対象ファイル: `src/gpu_job/launch_gate.py`
    *   実装内容: `_slice_status` と `_slice_blocks` で参照される文字列リテラルを名前付き定数に置換
*   **フェーズ2（ファイル内分割）**
    *   対象ファイル: `src/gpu_job/provider_contract_probe.py`
    *   実装内容: `_canary_job` 関数の内部実装を、Providerごとの関数（`_canary_job_runpod`, `_canary_job_vast`, `_canary_job_modal`）へ分割抽出
*   **フェーズ3（純粋関数分離）**
    *   対象ファイル: `src/gpu_job/provider_contract_probe.py` および `src/gpu_job/provider_contract_probe_helpers.py`（新規）
    *   実装内容: `_nested_first`, `_first_bool`, `_read_json` などの副作用のない内部ヘルパ関数を新規ファイルへ分離し、元のファイルからimportしてファイルに import 文を追加 import で結合
