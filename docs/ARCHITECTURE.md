# アーキテクチャ

## アプリの位置づけ

```
frappe ── erpnext ── japan_compliance（会計・消費税・インボイス・帳簿）
                └── hrms ── japan_hr_compliance（人事・給与・社保・源泉）
                                   └──（JP Core を参照）→ japan_compliance
```

- `japan_hr_compliance` は `hrms` と `japan_compliance` を前提とする（`required_apps`）。
- 会計だけを使う利用者に Frappe HR を強制しないため、`japan_compliance` には人事を入れない。

## 依存の向き

- 可：`japan_hr_compliance` → `japan_compliance` / `hrms` / `erpnext` / `frappe`
- 不可：`japan_compliance` → `japan_hr_compliance`、および特定企業の固有アプリへの依存

## マスタの原則

社会保険料率、雇用保険料率、標準報酬月額の等級表、源泉徴収税額表、通勤手当の
非課税限度額などは、適用期間つきのマスタとして持つ。改定はマスタの行を追加して
行い、過去の給与の再計算結果が変わらないようにする。
