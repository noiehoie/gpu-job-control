結論は `no` です。現行の基本計画はコード事実の大筋は合っていますが、「最小安全集合」に過剰案が 2 件混ざっています。`all green` 最優先なら、その 2 件を落としてから最終計画へ進むべきです。

## 指摘
### 1. 事実誤認
事実誤認はありません。次の認識は実コードと一致しています。

| 判定 | ファイル | 関数 | 行番号 | 行種別 | 監査結果 |
|---|---|---|---:|---|---|
| 維持 | `scripts/runpod-asr-serverless-contract-probe.py` | `_disable_endpoint_rest` | 615-621 | 定義行 | `template_id` は未使用です。 |
| 維持 | `scripts/runpod-asr-serverless-contract-probe.py` | `main` | 180 | 呼び出し行 | `_disable_endpoint_rest(..., template_id=...)` の呼び出しはコード上ここだけです。 |
| 維持 | `scripts/vast-pyworker-serverless-contract-probe.py` | `_lookup_template` | 617-625 | 定義行 | `_lookup_template_by_id` と同型です。 |
| 維持 | `scripts/vast-pyworker-serverless-contract-probe.py` | `_lookup_template_by_id` | 628-636 | 定義行 | `_lookup_template` と同型です。 |

### 2. 却下する過剰案
| 判定 | ファイル | 関数 | 行番号 | 行種別 | 却下理由 |
|---|---|---|---:|---|---|
| 却下 | `scripts/runpod-asr-serverless-contract-probe.py` | `_create_template` / `_create_endpoint` / 新規 `_is_rest_api` | 384, 402 | 分岐行 | 重複は 2 箇所だけです。外部挙動は変わらず、green も増えず、純削減もほぼ 0 行です。最小安全ではありません。 |
| 却下 | `scripts/vast-pyworker-serverless-contract-probe.py` | `main` | 146, 162, 172 | 呼び出し行 | `_read_payload()` の再読込は最大 3 回の小 I/O です。利益が小さく、読込タイミング変更という不要差分を増やします。最小安全ではありません。 |

## 最小安全候補
優先順位はこの 2 件です。

| 優先 | ファイル | 関数 | 行番号 | 行種別 | 残す理由 |
|---|---|---|---:|---|---|
| 1 | `scripts/runpod-asr-serverless-contract-probe.py` | `_disable_endpoint_rest` / `main` | 615-621 / 180 | 定義行 / 呼び出し行 | 未使用引数 `template_id` の削除だけです。PATCH 先、payload、戻り値 shape、`cleanup["steps"]` のキーを不変にできます。 |
| 2 | `scripts/vast-pyworker-serverless-contract-probe.py` | `_lookup_template` / `_lookup_template_by_id` / `_resolve_template_record` | 617-636 / 674 / 679 / 686 / 691 | 定義行 / 呼び出し行 | 完全重複の内部共通化です。`allow_failure=True`、先頭要素採用、`{}` fallback、`vast_template_lookup.json` の書込条件を不変にすれば安全です。 |

## 確認出力
実際の検索出力は次です。

```text
rg "_disable_endpoint_rest\(" ...
scripts/runpod-asr-serverless-contract-probe.py:180: disabled = _disable_endpoint_rest(...)
scripts/runpod-asr-serverless-contract-probe.py:615: def _disable_endpoint_rest(...)
```

```text
rg "_lookup_template_by_id\(|_lookup_template\(" ...
scripts/vast-pyworker-serverless-contract-probe.py:617: def _lookup_template(template_hash: str)
scripts/vast-pyworker-serverless-contract-probe.py:628: def _lookup_template_by_id(template_id: str)
scripts/vast-pyworker-serverless-contract-probe.py:674: template = _lookup_template(args.template_hash)
scripts/vast-pyworker-serverless-contract-probe.py:679: template = _lookup_template_by_id(args.template_id)
scripts/vast-pyworker-serverless-contract-probe.py:686: template = _lookup_template(inferred_hash)
scripts/vast-pyworker-serverless-contract-probe.py:691: template = _lookup_template_by_id(inferred_id)
```

```text
rg "routing_by_module_enabled" ...
target 2 files: hit なし
```

最終判定は `no` です。最終計画に進める条件は、`_is_rest_api()` 抽出案と `_read_payload` キャッシュ案を落とし、上の 2 件だけを残すことです。

