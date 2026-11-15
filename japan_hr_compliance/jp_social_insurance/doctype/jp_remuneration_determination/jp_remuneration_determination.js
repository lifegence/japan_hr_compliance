// 標準報酬月額の決定処理（SIR-40〜48）。給与実績からの集計
frappe.ui.form.on("JP Remuneration Determination", {
	refresh(frm) {
		if (frm.doc.docstatus === 0 && frm.doc.employee) {
			frm.add_custom_button(__("給与実績から集計"), () => frm.call("fill_months").then(() => frm.dirty() || frm.save()));
		}
	},
});
