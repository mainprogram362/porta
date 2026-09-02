# PORTA

PORTAは、メディア整理・ファイル操作・動画変換・ローカルAIなどをまとめた、
個人用・ローカル専用のアプリ群です。

## 構成

```text
porta/
├── persistent_settings_location.txt  # 外部ユーザー領域への低依存の案内札（Git管理外）
├── core/                     # 単独でも動く低依存のPORTA Core
├── standalone_apps/          # 単体起動できる低依存ツール（任意）
├── integrated_backends/      # PORTAから呼ぶ同梱実行エンジン
├── scripts/
│   └── main.py                 # 通常利用の起動入口
├── src/
│   ├── apps/                   # 完成した画面とアプリ固有の処理
│   ├── automation/             # 明示的なLinuxデスクトップ操作の薄い境界
│   ├── foundation/             # パス・設定・実行制御などの基盤処理
│   ├── gui/                    # 再利用できる画面部品・固定アセット
│   └── media/                  # 動画・ダウンロード・メディア情報の基盤処理
├── requirements.txt
└── README.md
```

`apps/launcher/catalog.py` がメインメニューに出す完成アプリを登録します。
空の分類はメインメニューに表示されません。

`core/main_menu.sh` はPORTAと同梱しますが、Python・仮想環境・Qtへ依存しません。
`core/` だけを切り離した場合もCOREメニューと自己診断を利用でき、親にPORTA本体の
起動環境がある場合だけメインメニュー起動を追加で利用します。

```bash
bash core/main_menu.sh --menu-only
```

`standalone_apps/` の各ツールは共通の `start.sh` から直接起動でき、COREのライブラリや
Pythonへ依存しません。緊急時にも使える「独立ツール」の既定置き場ですが、
PORTAとCOREはこのフォルダがなくても起動できます。

`integrated_backends/` は、7-ZipなどをPORTAが内部機能から呼び出すための実行エンジン置き場です。
単体起動を約束せず、なくてもPORTAの起動は妨げませんが、対応する機能だけが利用不可となります。

`__pycache__`、`.pytest_cache`、`.ruff_cache` は生成物であり、ソース構造には含めません。

外部のユーザー領域は場所を設定して使い、アプリの親フォルダを暗黙の置き場とは扱いません。
その中を `config/`、`local_data/`、`private/`、`cache/` に分けます。アプリ直下には
この外部領域を指す `persistent_settings_location.txt` だけを置きます。
案内札はコメントと空行を除き、絶対パスまたは案内札を基準にした
`porta_user` などの相対パスを1行だけ書きます。`~` と環境変数は展開しません。

```text
# PORTA_LOCATION_V1
porta_user
```

```text
任意の場所/porta_user/
├── config/
│   └── shared/
│       ├── standalone_apps_location.txt    # 独立ツール置き場（1件）
│       └── external_program_locations.txt  # 明示登録する外部プログラム置き場
├── local_data/
│   ├── ai/models/
│   ├── ai/runners/
│   ├── dictionaries/
│   └── templates/
├── private/
└── cache/
```

## 内部の境界

- `foundation` はQtや個別アプリを知らない共通基盤です。JSON設定の配置・原子的保存もここで統一します。
- `media` は画面を持たないメディア処理です。アプリ画面から再利用します。
- `gui` はアプリ固有の文言や保存処理を持たない共通部品です。パス入力は小入力・一覧表・OS連携に分離しています。
- `apps` は画面と操作手順を組み立てます。大きな画面固有処理は、同じアプリ配下のworkflowやadapterへ分離します。

完成画面の外周余白は `AppPageLayout`、最上部の「戻る・画面名・永続設定」は
`AppHeader` を使います。画面固有の操作欄は、その下で用途ごとに組み立てます。

依存方向は原則として `apps → gui / media / foundation` とし、基盤側から完成画面をimportしません。
メインメニューへ未登録で、公開APIからも参照されない試作はソースへ残さず、必要になった時点で現在の基盤上に作り直します。

## 永続設定とプライバシー

通常利用では、作業対象・操作結果・画面状態を記録しません。設定の編集または
出力を明示実行した場合だけ、その対象へ書き込みます。個人環境に依存する設定は
外部ユーザー領域の `config/` に置き、Git管理しません。保存先の変更・初期化はアプリ内の
「CONFIG・永続設定」から行えます。

「CONFIGを初期化」は現在のCONFIGを同じ親フォルダの
`config_backup_YYYYMMDD_HHMMSS/` へ退避してから、全アプリの設定雛形を新しく作ります。
雛形の準備や切り替えに失敗した場合は、初期化前のCONFIGへ戻します。

CoreとPythonは、PORTAと切り離して単体起動できる「独立ツール」と、明示登録する「外部プログラム」を別々のTXTから読みます。
`standalone_apps_location.txt` の雛形には `@PORTA/standalone_apps` を入れ、フォルダを移動しても現在のPORTAを基準に解決します。変更は可能ですが非推奨です。
`external_program_locations.txt` は雛形で空にし、ユーザーが明示した場所だけを1行に1つ読みます。
どちらも各置き場の直下にある `start.sh` 付きフォルダを同じ方式で探し、設定や置き場がなくてもCoreとPythonは起動できます。

Git/GitHubへ送信する機能はアプリにありません。ファイル操作は明示実行時だけ行い、
コピーの途中失敗では、その実行で作成した出力だけを削除する簡易ロールバックを試みます。

## 起動

```bash
cd porta
./start.sh
```

`start.sh` は自分の位置からPORTAルートを決めます。`.venv` がない場合やPORTAの移動を検出した場合は、
確認後にその場所用の仮想環境を作り、CONFIG・AIモデル・ユーザーデータには触れません。
`scripts/main.py` も必要な `src/` を自動でimportパスへ追加するため、`PYTHONPATH` の指定は不要です。

## テスト

自動テストはpytestに統一しています。

```bash
cd porta
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```
