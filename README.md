# Japan HR Compliance

Japanese HR and labor compliance app for Frappe HR (hrms) v16. It currently covers the
Japanese employee master (worker roster, employment contracts, My Number, labor condition
notices, onboarding/offboarding procedure tasks), statutory annual paid leave, and social
and employment insurance premiums. Payroll and withholding tax are planned. **Not legal
advice; do not use in production until the calculations have been verified by qualified
professionals (a licensed labor and social security attorney / tax accountant).**
Licensed under GPL-3.0-or-later.

---

Frappe HR（hrms）v16 向けの日本の人事労務ローカライズアプリです。会計ローカライズの
[`japan_compliance`](https://github.com/lifegence/japan_compliance) と対になるアプリで、
和暦・都道府県・法人番号など日本共通の部品は `japan_compliance` の JP Core を使います。

> **免責**
>
> - 本アプリは開発中（v0.1.0）です。社会保険労務士・税理士による計算・帳票の確認が完了するまで、
>   本番の人事労務・給与事務には使わないでください。
> - 本アプリおよび付属の文書は法的助言ではありません。法令・通達の解釈や個別の事案の判断は、
>   利用者の責任で専門家に確認してください。
> - 法令の改正に追随していることは保証しません。料率・等級表・期限等はマスタの値を利用者が確認してください。

## 実装済みの機能

### JP HR Core（従業員マスタ）

- Employee の日本向け項目（氏名のカナ、社員番号の一意性、住所履歴、扶養親族、振込口座など）。
  Employee は改変せず `doc_events` で拡張します
- 労働者名簿（労基法107条・労基則53条の記載事項）と事業場別の常時使用労働者数のレポート
- 事業場マスタ・業務の種類・通常の労働者の所定労働条件
- 雇用契約の条件の履歴（任意の日付の条件を
  `japan_hr_compliance.jp_hr_core.contract.get_contract_at(employee, date)` で取得）と
  有期契約の満了予告
- マイナンバー：暗号化して保存、画面は常に全桁マスク、事務取扱担当者だけが理由を入力して復号・出力、
  すべての操作をアクセスログに追記、書類ごとの保存期限から廃棄可能日を算出
- 労働条件通知書の作成と印刷
- 口座振込の同意・電子交付の承諾の記録、従業員ポータルからの変更申請（住所・振込口座・扶養）
- 入退社・扶養異動の手続きタスク（社会保険・雇用保険・源泉徴収票・住民税）。期限は制度ごとの規則マスタで変更できます
- 日本向けの既定ロールと権限

### JP Leave（年次有給休暇）

- 法定の付与表と会社独自の付与テーブル（法定を下回る行の検出）、比例付与
- 出勤率の算定、個別基準日・斉一的付与・前倒し・分割付与
- 付与予定の作成と基準日の確定（日次処理）、付与漏れの通知
- 取得の充当（古い付与から先入先出）、半日単位、2年での失効
- 年5日の取得義務の管理と通知（本人の請求・計画的付与・時季指定の区分）
- 年次有給休暇管理簿・年5日の取得状況のレポート、従業員ポータルの残日数表示

### JP Social Insurance（社会保険・雇用保険）

- 健康保険・介護保険・厚生年金・子ども・子育て支援金・雇用保険・労災保険の料率と
  標準報酬月額の等級表を、適用期間つきの版として管理（下書き → 確認済。確認済の版だけを計算に使用）
- 料率・等級表の CSV 取込みと保険料額表との突合
- 適用の判定（短時間労働者の要件・人数の警告）と見直しのレポート
- 標準報酬月額の決定（資格取得時決定・定時決定・随時改定・保険者算定・休業終了時改定）
- 月次・賞与の保険料計算（端数処理、標準賞与額の上限と累計）、給与明細への控除行・事業主負担行の書き込み
- 産前産後休業・育児休業等の保険料免除
- 保険料台帳と、保険料総額の試算と納入告知額の差異のレポート

## 未実装（今後）

| モジュール | 内容 |
| :--- | :--- |
| JP Payroll | 労働時間の集計と月次給与計算 |
| JP Withholding | 源泉所得税・住民税の特別徴収・通勤手当の非課税 |

モジュールの枠だけがあり、機能はまだありません。

## 料率・等級表はマスタとして登録してください

社会保険料率、雇用保険料率、労災保険料率、標準報酬月額の等級表、標準賞与額の上限などの値は、
本アプリに同梱していません（テストで使う値は例示値です）。利用者が日本年金機構・全国健康保険協会
（協会けんぽ）・各健康保険組合・厚生労働省などの公表資料から、適用期間つきのマスタとして登録してください。
登録した版は、出典（資料名・公表日・URL）と確認者・確認日を入力して「確認済」にしたものだけが計算に使われます。

一部の初期データ（手続きの期限、マイナンバーを記載した書類の保存期限）は「仮の設定」を含みます。
画面で「仮の設定」と表示されるものは、利用者が法令に照らして確認してから使ってください。

## 前提アプリ

- Frappe Framework v16
- ERPNext v16
- Frappe HR（hrms）v16
- [`japan_compliance`](https://github.com/lifegence/japan_compliance)（会計ローカライズ。JP Core を使用）

## インストール

```bash
bench get-app --branch version-16 erpnext
bench get-app --branch version-16 hrms
bench get-app https://github.com/lifegence/japan_compliance
bench get-app https://github.com/lifegence/japan_hr_compliance
bench --site <site> install-app erpnext hrms japan_compliance japan_hr_compliance
```

テストの実行：

```bash
bench --site <site> set-config allow_tests true
bench --site <site> run-tests --app japan_hr_compliance
```

## 設計方針

1. Frappe HR・ERPNext・Frappe の本体を直接改修せず、本アプリ側で完結させる
2. 料率・税額表・限度額は適用期間を持つマスタとして管理し、コードにハードコードしない
3. 帳票は標準の Print Format（Jinja）で実装する
4. 依存は `japan_hr_compliance → japan_compliance` の一方向。特定企業の固有アプリに依存しない

詳細は [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) を参照してください。

## コード中の要件 ID について

コメントやテストに出てくる `EMP-xx`（従業員マスタ）・`LVR-xx`（年次有給休暇）・`SIR-xx`（社会保険・雇用保険）・
`B-x`（機能領域）は、開発時の要件定義の番号です。「規則 2.4」「8章 #1」のような章番号も同じ要件定義の章を指します。
要件定義の文書は公開していませんが、各機能の仕様はコードのコメントとテストに記載しています。

## 貢献・脆弱性の報告

[CONTRIBUTING.md](CONTRIBUTING.md) と [SECURITY.md](SECURITY.md) を参照してください。

## ライセンス

GPL-3.0-or-later。[LICENSE](LICENSE) を参照してください。

Copyright (C) 2026 ライフジェンス株式会社
