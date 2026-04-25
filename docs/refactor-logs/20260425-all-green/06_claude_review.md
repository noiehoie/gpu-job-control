## 計画審査結果

### 前提条件の不成立

**現在テストが all green ではない（8 failed, 337 passed）。計画のステップ0「all green の事実をコマンドで固定」が不可能。**

失敗テスト:
- `test_asr_diarization_contract.py` 4件
- `test_production_contracts.py` 1件
- `test_provider_module_contracts.py` 1件
- `test_requirement_registry.py` 1件
- `test_standardized_planning.py` 1件

失敗原因: Vast.ai API への proxy 接続エラー（ネットワーク層）。

---

### 最小安全集合の選別

計画8ステップのうち、**今すぐ実施すべきはゼロ**。理由:

1. **ステップ0が不成立**: green 固定ができない
2. **green を壊すリスク > 得られる価値**: 全ステップが「可読性改善」だが、1300行の `provider_contract_probe.py` は現在動作しており、テスト失敗はネットワーク層
3. **過剰な抽象化の危険**: ステップ4「表駆動化」、ステップ3「`DEFAULT_CONTRACT_PROBES` 共通化」は Gemini 監査で却下された builder 提案と実質同じリスク

---

### 各ステップの危険性評価

#### ステップ1: ヘルパ関数の内部モジュール分離
- **危険性**: 中。import 経路変更でテストが割れる可能性
- **価値**: 低。`_read_json` 等の分離は可読性以外の利益なし
- **判定**: **削除**

#### ステップ2: `parse_contract_probe_artifact()` の内部段階分割
- **危険性**: 中〜高。352行の関数を5段階に分割は大規模変更
- **価値**: 低。現在の関数は長いが動作している
- **判定**: **削除**

#### ステップ3: `DEFAULT_CONTRACT_PROBES` のリテラル重複削減
- **危険性**: 高。Gemini が指摘した「キー欠落で green が割れる」リスクそのもの
- **価値**: 低。`[*DEFAULT_REQUIRED]` で既に共通化済み（57行、71行等）
- **観察**: 現状は各エントリ10行程度で読みやすい。共通化は過剰
- **判定**: **削除**

#### ステップ4: 多辞書フォールバック抽出と表駆動化
- **危険性**: 高。`_first_bool` / `_first_string` / `_nested_first` の反復を「スキーマから実行」に変えるのは大規模抽象化
- **価値**: 低。594行 `_workspace_contract_summary()` は冗長だが、優先順序が明示的で安全
- **観察**: `workspace_observation_coverage()` の12カテゴリ `_coverage_entry` は820〜869行で既に構造化されている
- **判定**: **削除**

#### ステップ5: `_canary_job()` の provider 別分岐の分割
- **危険性**: 中。分割先を間違えると canary job 生成が壊れる
- **価値**: 低。`_canary_job()` が何行か不明だが、分割の緊急性なし
- **判定**: **削除**

#### ステップ6: `plan_quote` 投影の DRY 化
- **危険性**: 高。Gemini 訂正で「`workspace_registry.py` に `plan_quote` は存在しない」と明示されたが、計画は「実際に重複している層だけを触る」と曖昧
- **価値**: 低。`execution_record.py` の `_plan_quote_from_job()` は123行から始まる30行程度の関数で、重複の実態が不明
- **観察**: `contracts.py` / `requirements.py` 内に同等ロジックがあるか未確認
- **判定**: **削除**（実態確認前の DRY 化は危険）

#### ステップ7: `launch_gate.py` の manifest 依存文字列の定数化
- **危険性**: 低〜中。`_slice_status()` / `_slice_blocks()` は185〜191行の単純な文字列参照
- **価値**: 低。定数化は可読性のみ。現在の実装で意味は明確
- **判定**: **削除**

#### ステップ8: Provider 分割
- **危険性**: 高。計画自体が「本計画の範囲外」と明示
- **判定**: **削除**（範囲外）

---

### 実装対象ファイルと具体的変更内容

**なし。全ステップを削除。**

---

### 採用すべき最終計画案

**計画案: 実装作業をゼロにする**

1. **テスト失敗を先に修正せよ**: Vast.ai proxy エラーの8件失敗を解決し、all green を確立してからリファクタを検討
2. **green 確立前のリファクタは禁止**: 現在のコードは動作している。可読性のみを理由とした1300行の関数分割は green 維持コストを上回らない
3. **Council Gate Protocol 違反**: AGENTS.md は「material work には research / design / code / audit の council 記録」を要求。本計画には `docs/council-audit/` の JSONL 記録が存在しない。計画の実行前に council 監査を完了せよ
4. **計画の前提条件を明示せよ**: 「現在 all green」でない場合はステップ0が成立しない。この前提を計画書の冒頭に記載すべきだった
5. **今後の方針**: green 確立後、以下の順で検討せよ
   - 新機能追加時に直接関係する関数のみを局所リファクタ
   - 1300行の `provider_contract_probe.py` を触るなら、まず100行以下の別ファイルで試行し、テストが green のまま段階的に移行

**結論: この計画は採用すべきでない。全ステップを破棄し、テスト修正を優先せよ。**
