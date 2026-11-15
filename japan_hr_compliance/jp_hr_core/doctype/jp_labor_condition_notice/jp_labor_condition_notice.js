// 労働条件通知書（EMP-50〜52）
frappe.ui.form.on("JP Labor Condition Notice", {
	setup(frm) {
		frm.set_query("contract", () => ({ filters: { employee: frm.doc.employee, docstatus: 1 } }));
	},
	refresh(frm) {
		if (frm.doc.docstatus === 0 && frm.doc.missing_items) {
			frm.dashboard.set_headline_alert(`${__("未入力の明示事項")}：${frappe.utils.escape_html(frm.doc.missing_items.split("\n").join("、"))}`, "orange");
		}
	},
});
