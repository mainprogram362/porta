# src の役割と依存方向

## 配置

通常のアプリ入口は、一つの機能フォルダにつき一つとする。
分類メニューはアプリではなく、その入口を並べるための分類。
自動操作の作成・使用、ローカルAIのチャット・設定は各アプリ内の画面として扱う。
PORTA全体の管理画面は `apps/porta_control` が担当し、「PORTAの画面」から
メニュー・作業タブ・分離ウィンドウ・対応表をまとめて確認する。一つのPORTAウィンドウ内の
通常タブは同じプロセスで管理し、明示的に分離したトップレベルウィンドウだけを別プロセスとする。
画面ホストは `apps/launcher`、プロセス制御の実処理は `runtime` に置く。
移動前の設定・診断・一覧モジュールは互換入口だけを残す。

| パッケージ | 担当 |
| --- | --- |
| `foundation` | パス、ファイル、文字列などの基本処理とその一括操作。旧 import の互換入口も残す |
| `runtime` | プロセス実行・登録・単一起動・作業状態・進捗・一時的な作業間受け渡し |
| `settings` | 永続設定の読み書き、設定内のパス記法、ユーザー領域 |
| `records` | 対応表のデータ構造、抽出・加工・並べ替え、独立対応表との通信 |
| `automation` | デスクトップ操作とブラウザ自動化の共通処理 |
| `media` | メディア取得・情報・変換などの共通処理 |
| `gui` | アプリ間で共有するQt部品と画面の振る舞い |
| `apps` | 各画面と、その画面・機能固有の組み立て |

フォルダは担当分野で分ける。基本関数と組み合わせ処理は、必要に応じて
ファイル単位で分ける。関数の長さや利用画面数だけで配置を決めない。
例えばパス一覧ボックスは `gui/composites`、そのパス判定は `foundation`、
ファイルマネージャー固有の列や操作の設定は `apps/file_tools/file_manager` が担当する。

## 共通処理の依存

- `apps` は共通パッケージと `gui` を使う。
- 共通処理から `apps` の画面を import しない。
- `runtime` / `settings` / `records` / `automation/browser` は `gui` に依存しない。
- 対応表のモデル・加工は通信や画面を読み込まずに使える。
- QtのIPCは `runtime/single_instance.py`、`records/record_service.py` に存在する。
  「画面なし」と「Qt依存なし」は異なる。
- パッケージの `__init__.py` は軽く保ち、実装モジュールを指定して読み込む。

## 今回の移行

`foundation` から、同名の実装モジュールを次へ移した。

- `runtime`: `process`, `process_registry`, `process_control`, `managed_process`,
  `instance_presence`, `runtime_activity`, `operation_progress`, `single_instance`,
  `work_process`, `transient_paths`
- `settings`: `persistent_settings`, `json_settings`, `user_space`, `path_tokens`
- `records`: `record_bundle`, `record_bundle_fields`, `record_bundle_editing`, `record_service`

`apps/automation_tools/browser` の `cartridge` と `transport` は
`automation/browser` へ移した。ブラウザの画面・Qt worker・実行手順の画面連携は
引き続き `apps` にある。Firefox内の実装は `extensions/porta_firefox` にある。
今回の整理で新しい自動化APIやPythonカートリッジ実行機能を追加したわけではない。

## 互換性

旧モジュールは削除せず、新モジュールへの `sys.modules` エイリアスとした。
旧名と新名で読み込んでも同じモジュール・クラス・共有状態を使う。
単なる関数の再exportではないため、従来のモジュールへのモックも新実装に届く。
旧名で保存されたPythonクラス参照も、対応する属性が存在すれば解決できる。

`src` と `scripts` の内部参照は新配置を使う。既存テストには旧名を使うものも残し、
互換入口経由の動作も検証する。ユーザー作成スクリプトの一斉書き換えは不要。
互換入口は新しい処理の追加先にしない。今後の修正は新配置の実装で行う。

新配置への反映にはPORTAの再起動が必要。実行中の対応表はメモリ内データなので、
必要な内容を保存してから各プロセスを終了する。配置整理は実行中プロセスを終了しない。
