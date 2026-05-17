# Arduino GreenPAK Writer

Arduino GreenPAK Writer は、Arduino UNO を I2C ブリッジとして使用し、PC から Renesas / Dialog GreenPAK SLG46826G へ NVM HEX ファイルを書き込むための簡易ライターです。

GreenPAK Designer から出力した Intel HEX ファイルを PC 側ツールで選択し、Arduino UNO 経由で SLG46826G の NVM 構成領域へ書き込みます。Arduino IDE のシリアルモニタから手入力する必要はありません。

## 対象デバイス

現在の主対象は以下です。

- Renesas / Dialog GreenPAK SLG46826G
- Arduino UNO
- Windows PC + Python 3

SLG46826G の NVM 構成領域 page 0〜14 への書き込みを想定しています。
SLG46824/SLG46826 の NVM Registers 領域では、page 15 は Service Page として予約されています。
このページは工場出荷時に事前書き込みされた情報を含み、ユーザーは読み出しできますが書き込みできません。
そのため、本ツールでは page 0〜14 のみを書き込み・verify対象とし、page 15 はスキップします。

EEPROM領域は通常のロジック構成には不要なため、このライターでは書き込みません。

## 主な機能

- PC側GUIからHEXファイルを選択
- Arduino UNO経由でSLG46826GへI2C書き込み
- Intel HEX形式の読み込み
- 256バイトNVMイメージの解析
- NVM page 0〜14の書き込み
- page単位のErase / Write / Verify
- page 15のサービスページをスキップ
- Raw I2C Scan
- Control Code指定
- 書き込み後の読み出し確認
- Arduino IDEシリアルモニタ不要

## フォルダ構成

```text
Arduino_GreenPAK_Writer/
├─ UNO_BRIDGE_SLG46826G_v06/
│  └─ UNO_BRIDGE_SLG46826G_v06.ino
├─ pc_writer/
│  └─ greenpak_hex_writer_v06.py
└─ README.md
```

## 必要なもの

### ハードウェア

- Arduino UNO
- SLG46826G
- TSSOP-20変換基板または書き込み治具
- 4.7kΩ抵抗 ×2
- 0.1uFバイパスコンデンサ
- ジャンパ線
- USBケーブル

### ソフトウェア

- Arduino IDE
- Python 3
- pyserial

pyserial は以下でインストールします。

```bash
pip install pyserial
```

## 配線

SLG46826G TSSOP-20版を前提とした最小配線です。

```text
Arduino UNO A4  ---- SLG46826G SDA
Arduino UNO A5  ---- SLG46826G SCL
Arduino UNO GND ---- SLG46826G GND

3.3V ---- 4.7kΩ ---- SDA
3.3V ---- 4.7kΩ ---- SCL

SLG46826G VDD  ---- 3.3V
SLG46826G VDD2 ---- 3.3V
SLG46826G GND  ---- GND

VDD-GND間  ---- 0.1uF
VDD2-GND間 ---- 0.1uF
```

注意：

- 書き込み対象ICを既存回路に載せたまま使う場合、他のICがSDA/SCLを引っ張っていないか確認してください。

## Arduino UNO側ファームの書き込み

Arduino IDEで以下のファイルを開きます。

```text
UNO_BRIDGE_SLG46826G_v06/UNO_BRIDGE_SLG46826G_v06.ino
```

ボードを `Arduino UNO` に設定し、通常通り書き込んでください。

書き込み後、Arduino IDEのシリアルモニタは閉じてください。PC側ライターがCOMポートを使用します。

## PC側ライターの起動

以下を実行します。

```bash
python pc_writer/greenpak_hex_writer_v06.py
```

GUIが起動したら、COMポートを選択します。

## Control Codeについて

SLG46826GのI2CアドレスはControl Codeに依存します。

実際の初期状態では Control Code = 0 の個体があります。Raw I2C Scanで以下のように表示される場合、Control Code = 0 で使用します。

```text
RAWSCAN: OK RAWSCAN 03
PING: OK PING REG=1 NVM=1
```

このプロジェクトでは、まず Control Code = 0 を試すことを推奨します。

接続確認が成功すると、ログは概ね以下のようになります。

```text
UNO: OK GPUNO_SLG46826_BRIDGE 0.6
RAWSCAN: OK RAWSCAN 03
SCAN: OK SCAN 0
PING: OK PING REG=1 NVM=1
```

## HEXファイルの用意

GreenPAK Designerで設計を作成し、NVM HEXを出力します。

```text
File → Export → Export NVM
```

出力されたIntel HEXファイルをPC側ライターで選択します。

正常なファイルは、16バイト × 16行の256バイト構成になります。

例：

```text
:1000000089411C05E23C81B00C0A214D50C008CB4F
...
:00000001FF
```

## 書き込み手順

1. Arduino UNOへブリッジファームを書き込む
2. SLG46826GとArduino UNOをI2C接続する
3. PC側ライターを起動する
4. COMポートを選択する
5. Control Codeを指定する
6. `Raw I2C Scan` または `接続確認` を実行する
7. GreenPAK Designerから出力したHEXファイルを選択する
8. 書き込みを実行する
9. Verify結果を確認する
10. 必要に応じてSLG46826Gの電源を再投入する

## 書き込みログ例

```text
page 00: 89 41 1C 05 E2 3C 81 B0 0C 0A 21 4D 50 C0 08 CB
  -> OK PROGRAMMED
page 01: A0 54 56 04 00 00 00 00 00 00 00 00 00 00 00 00
  -> OK PROGRAMMED
page 02: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
  -> OK PROGRAMMED
...
page 14: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
  -> OK SKIPPED
Verify中...
```

`OK PROGRAMMED` は書き込み成功です。

`OK SKIPPED` は、そのページがすでに目的値と一致していた、または書き込み不要だったことを示します。エラーではありません。

## Verifyについて

このライターでは、通常の構成NVMとして page 0〜14 をverify対象にします。

page 15 はサービスページのため、GreenPAK DesignerのHEXと読み出し値が一致しない場合があります。これは異常ではありません。

判断基準：

```text
page 0〜14 が一致 → 書き込み成功
page 15 だけ違う → 正常、無視
page 0〜14 に差分あり → 書き込み失敗または通信不良
```

## EEPROMについて

SLG46826Gには構成NVMとは別にEmulated EEPROM領域があります。

このライターではEEPROMを書き込みません。

通常のGreenPAKロジック構成、アドレスデコード、ゲートロジック、CPLD的な置き換え用途ではEEPROMは不要です。

EEPROMを書き込む必要があるのは、以下のような用途だけです。

- 個体ごとのID保存
- 補正値や設定値の保存
- 外部MCUからSLG46826G内のEEPROM領域をデータ保存先として使う設計
- 初期データテーブルをEEPROMへ入れる設計

## トラブルシューティング

### `ERR UNKNOWN_CMD` が出る

UNOに正しいブリッジファームが書き込まれていない可能性があります。

対策：

- `UNO_BRIDGE_SLG46826G_v06.ino` をUNOへ再書き込み
- Arduino IDEのシリアルモニタを閉じる
- 古い `.ino` ファイルが同じフォルダに残っていないか確認

Arduino IDEは同一フォルダ内の `.ino` をすべてコンパイルします。古いスケッチと新しいスケッチを同じフォルダに入れないでください。

### `PING: ERR NO_DEVICE` が出る

PCとUNOの通信は成功していますが、UNOからSLG46826Gが見えていません。

確認点：

- VDDが入っているか
- VDD2が入っているか
- GNDが共通になっているか
- SDA/SCLが逆になっていないか
- SDA/SCLに4.7kΩプルアップがあるか
- TSSOP-20のピン番号を逆に見ていないか
- 既存基板上の他の部品がI2Cラインを妨害していないか

### `RAWSCAN` に何も出ない

I2Cバス上に何も見えていません。

配線、電源、プルアップを確認してください。

### `RAWSCAN: OK RAWSCAN 03` なのにControl Code 1で失敗する

Control Codeを0にしてください。

例：

```text
Control Code = 0
```

### HEX読み込み時に「16進バイトダンプとしても256バイトではありません」と出る

HEXファイル形式がIntel HEXとして読めていません。

確認点：

- GreenPAK Designerの `Export NVM` で出力したファイルか
- ファイルが途中で切れていないか
- EOF行 `:00000001FF` があるか
- テキストエディタで余計な文字を入れていないか

## 現在の制限

- 主対象はSLG46826G
- NVM page 0〜14のみ書き込み
- page 15はスキップ
- EEPROMは未対応
- セキュリティロック設定済みの個体には書き込めない場合があります
- 量産用の高速ライターではありません
