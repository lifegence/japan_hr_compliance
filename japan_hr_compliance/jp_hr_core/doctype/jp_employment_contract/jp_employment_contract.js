// 雇用契約（EMP-20〜27）。確定済みの契約から労働条件通知書（EMP-50）を作る
frappe.ui.form.on("JP Employment Contract", {
	refresh(frm) {
		if (frm.doc.docstatus === 1 && frappe.model.can_create("JP Labor Condition Notice")) {
			frm.add_custom_button(__("労働条件通知書を作成"), () => {
				frappe.call({ method: "japan_hr_compliance.jp_hr_core.doctype.jp_labor_condition_notice.jp_labor_condition_notice.make_from_contract",
					args: { contract: frm.doc.name } }).then((r) => frappe.set_route("Form", "JP Labor Condition Notice", r.message));
			});
		}
		if (frm.doc.retro_impact) {
			frm.dashboard.set_headline_alert(__("遡及変更：既に付与・控除に使われた期間を含みます。影響の一覧を確認してください。"), "orange");
		}
	},
});
