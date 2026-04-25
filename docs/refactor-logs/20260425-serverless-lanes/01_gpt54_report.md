2ファイルとも全行確認を終えました。いま、`main()` の巨大分岐と小関数の重複を切り分けて、`green` を壊しにくい削減候補と触禁領域を整理しています。2ファイルを全行確認した。結論だけ先に書く。

- `scripts/runpod-asr-serverless-contract-probe.py` は、純粋な行削減余地が小さい。主対象は `main()` の責務分離と、死引数1件だけ。
- `scripts/vast-pyworker-serverless-contract-probe.py` は、明確な重複が複数ある。`_lookup_template()` 系と `_resolve_template_record()` から着手するのが最も安全。

## 1. 各ファイルで削減できる行の候補
`scripts/runpod-asr-serverless-contract-probe.py`

- `main()` の `if args.managed_create_surface == "graphql"` / `else` 分岐は、`endpoint create`、`endpoint disable`、`endpoint delete` で3回出る。分岐箇所を1か所に集約できる。
- `_disable_endpoint_rest(api_key, endpoint_id, *, template_id)` の `template_id` は未使用。削除候補。
- `main()` 内の `cleanup_skipped`、`_guard_clean(post_guard)`、`_startup_seconds_observed(run_result)` は複数回計算している。ローカル変数化で重複を減らせる。
- `main()` の `managed_resources` 分岐は、`plan決定`、`template準備`、`endpoint準備`、`submit/poll`、`cleanup`、`artifact集約` を1関数に詰めている。行数削減より先に責務分離で可読性を上げるべき。

`scripts/vast-pyworker-serverless-contract-probe.py`

- `_lookup_template()` と `_lookup_template_by_id()` は同型処理。1関数に統合できる。
- `_resolve_template_record()` は `if template: _write_json(... "vast_template_lookup.json" ...)` を4回繰り返している。共通化できる。
- `main()` は `_read_payload(args.worker_request_file or args.route_payload_file)`、`_read_payload(args.route_payload_file)`、`_read_payload(args.worker_request_file)` を別々に呼んでいる。先に読み込めば重複を削れる。
- `_sdk_response_ok()` と `_request_ok()` は、HTTPステータス判定と `error` ボディ判定が重複している。共通ヘルパー化できる。

## 2. 冗長分岐・重複処理・責務混在の箇所
`scripts/runpod-asr-serverless-contract-probe.py`

- `main()` の `if managed_resources:` / `else:` が長すぎる。`既存endpoint解決` と `managed作成` と `cleanup` と `artifact生成` が混在している。
- `main()` の `if args.managed_create_surface == "graphql"` / `else:` が重複している。

```109:123:scripts/runpod-asr-serverless-contract-probe.py
        if managed_resources:
            template, resolved_existing_template_id, template_provenance = _prepare_managed_template(
                api_key=api_key,
                args=args,
                plan=plan,
                artifact_dir=artifact_dir,
            )
            endpoint_input = dict(plan["endpoint"])
            endpoint_input["templateId"] = str(template["id"])
            if template_provenance.get("mode") == "managed_existing_template":
                endpoint_input = _apply_managed_template_endpoint_defaults(endpoint_input, template)
            if args.managed_create_surface == "graphql":
                endpoint = _create_endpoint_graphql(api_key, endpoint_input)
            else:
                endpoint = _create_endpoint(api_key, endpoint_input)
```

- 同じ `graphql/rest` 分岐が cleanup 側にもある。

```175:190:scripts/runpod-asr-serverless-contract-probe.py
        if endpoint and managed_resources:
            try:
                if args.managed_create_surface == "graphql":
                    disabled = provider._disable_endpoint(str(endpoint["id"]), template_id=str(endpoint["templateId"]))
                else:
                    disabled = _disable_endpoint_rest(api_key, str(endpoint["id"]), template_id=str(endpoint["templateId"]))
                cleanup["steps"].append({"disable_endpoint": disabled})
                time.sleep(3)
            except Exception as exc:
                cleanup["steps"].append({"disable_endpoint_error": str(exc)})
            try:
                if args.managed_create_surface == "graphql":
                    deleted = provider._delete_endpoint(str(endpoint["id"]))
                else:
                    deleted = _delete_endpoint_rest(api_key, str(endpoint["id"]))
```

`scripts/vast-pyworker-serverless-contract-probe.py`

- `_lookup_template()` と `_lookup_template_by_id()` は、検索キーだけが違う完全重複。

```617:636:scripts/vast-pyworker-serverless-contract-probe.py
def _lookup_template(template_hash: str) -> dict[str, Any]:
    query = f"hash_id == {template_hash}"
    result = _run_vast(["vastai", "search", "templates", query, "--raw"], allow_failure=True)
    payload = result.get("json")
    if isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, dict):
            return first
    return {}

def _lookup_template_by_id(template_id: str) -> dict[str, Any]:
    query = f"id == {template_id}"
    result = _run_vast(["vastai", "search", "templates", query, "--raw"], allow_failure=True)
    payload = result.get("json")
    if isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, dict):
            return first
    return {}
```

- `_resolve_template_record()` は、`lookup` と `artifact永続化` と `source決定` を同時にやっている。責務が混在している。
- `main()` は `endpoint/workergroup作成`、`template解決`、`SDK request`、`route fallback`、`cleanup`、`artifact生成` を全部持っている。

## 3. all green を壊さずに削れる最小安全候補
最小安全候補は次の2件。

- `scripts/runpod-asr-serverless-contract-probe.py` の `_disable_endpoint_rest()` から未使用の `template_id` を削る。これは外部I/O、artifact schema、provider adapter 呼び出し順を変えない。
- `scripts/vast-pyworker-serverless-contract-probe.py` の `_lookup_template()` と `_lookup_template_by_id()` を、内部共通関数1個に統合する。呼び出し側の戻り値型はそのまま維持できる。

この2件より先に `main()` を切り始める必要はない。`green` 優先なら、最初はここまでで止めるべき。

## 4. 関数単位の削減候補一覧
`scripts/runpod-asr-serverless-contract-probe.py`

- `main()`: 最優先の可読性改善対象。削減方法は `既存endpoint解決`、`managed作成`、`cleanup`、`artifact集約` の4分割。
- `_prepare_managed_template()`: `managed_template_id あり` と `managed_template_id なし` の2責務を分離すると短くできる。
- `_disable_endpoint_rest()`: 未使用引数 `template_id` を削除。
- `_create_template()`: 削減対象ではない。REST/GraphQL切替の境界。
- `_create_endpoint()`: 削減対象ではない。REST/GraphQL切替の境界。
- `_submit_payload()`: 削減対象ではない。成功条件の入力契約そのもの。
- `_normalized_output()`: 削減対象ではない。artifact整形契約そのもの。
- `_poll_serverless_run()`: 削減対象ではない。状態遷移サンプル採取を保持すべき。
- `_blocker_chain()`: 削減対象ではない。失敗分類の出力契約。

`scripts/vast-pyworker-serverless-contract-probe.py`

- `main()`: 最優先の可読性改善対象。`provision`、`template resolution`、`request path`、`cleanup/artifact` に分割可能。
- `_lookup_template()`: `_lookup_template_by_id()` と統合候補。
- `_lookup_template_by_id()`: `_lookup_template()` と統合候補。
- `_resolve_template_record()`: `lookup永続化` を外出しすれば短くなる。
- `_sdk_response_ok()`: `_request_ok()` と共通判定を抽出可能。
- `_request_ok()`: `_sdk_response_ok()` と共通判定を抽出可能。ただし最後の `url/route_attempts` 条件は残す。
- `_gpu_probe_from_request()`: `request_ok` と `worker_statuses` を引数でもらえば重複参照を減らせる。
- `_sdk_request()`: 削減対象ではない。Vast SDK との接続境界。
- `_route_probe()`: 削減対象ではない。route API のフォールバック境界。
- `_ensure_vast_api_key_env()`: 削減対象ではない。実行環境の鍵解決境界。

## 5. 絶対に触ってはいけない部分
- `scripts/runpod-asr-serverless-contract-probe.py` の `RunPodProvider` 呼び出し順。`cost_guard()`、`plan_asr_endpoint()`、`_api_snapshot()`、`_endpoint_health_sample()`、`_disable_endpoint()`、`_delete_endpoint()` の意味を変えてはいけない。
- `scripts/runpod-asr-serverless-contract-probe.py` の `_submit_payload()`。`official_audio_base64` と `custom_probe` の入力契約を削ると成功条件が変わる。
- `scripts/runpod-asr-serverless-contract-probe.py` の `_normalized_output()`。ここを崩すと `result.json` / `metrics.json` / `verify.json` / `probe_info.json` の整合が壊れる。
- `scripts/runpod-asr-serverless-contract-probe.py` の `cleanup["ok"]` 算出式。課金リーク防止の境界。
- `scripts/vast-pyworker-serverless-contract-probe.py` の `_sdk_request()`。`ManagedEndpoint.request()` の返却 shape を downstream が前提にしている。
- `scripts/vast-pyworker-serverless-contract-probe.py` の `_route_probe()`。`endpoint`、`api_key`、`cost` を含む payload 形状を変えてはいけない。
- `scripts/vast-pyworker-serverless-contract-probe.py` の `cleanup["ok"]` 算出式。workergroup / endpoint の後始末確認そのもの。
- 両ファイルの `result`、`metrics`、`verify`、`probe_info` の top-level key 名。artifact contract なので削ってはいけない。
- `routing_by_module_enabled` はこの2ファイル内に存在しない。したがって、この分析に基づく実装では新しい module routing を導入しない。`routing_by_module_enabled=false` は外部でも不変のまま維持する。

## 6. 実装順序
1. `scripts/runpod-asr-serverless-contract-probe.py` の `_disable_endpoint_rest()` から未使用 `template_id` を削る。
2. `scripts/vast-pyworker-serverless-contract-probe.py` の `_lookup_template()` と `_lookup_template_by_id()` を統合する。
3. `scripts/vast-pyworker-serverless-contract-probe.py` の `_resolve_template_record()` から `template lookup artifact 書き出し` を小ヘルパーに出す。
4. ここまでで `green` を確認する。
5. 次に両ファイルの `main()` だけを分割する。先に `cleanup` を切り出し、その後 `request path`、最後に `artifact集約` を切る。
6. `_sdk_response_ok()` / `_request_ok()` の共通化は最後に回す。ここは見た目より壊しやすいので、前段が全部 green の後だけ着手する。

必要なら次に、2ファイルだけを対象にした「削減実施パッチ案」を関数単位で順番付きで書ける。
