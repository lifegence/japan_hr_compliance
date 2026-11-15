// 標準報酬月額の履歴（SIR-12・47）。資格取得時の等級の提案
frappe.ui.form.on("JP Standard Remuneration History", {
	refresh(frm) {
		if (frm.doc.docstatus === 0 && frm.doc.employee && frm.doc.effective_from) {
			frm.add_custom_button(__("資格取得時の等級を提案"), () => frappe.call({
				method: "japan_hr_compliance.jp_social_insurance.doctype.jp_standard_remuneration_history.jp_standard_remuneration_history.propose_acquisition_standard",
				args: { employee: frm.doc.employee, effective_from: frm.doc.effective_from, remuneration: frm.doc.remuneration },
			}).then((r) => {
				if (!r.message) return frappe.msgprint(__("報酬月額の見込みを入力してください"));
				frm.set_value(r.message);
			}));
		}
	},
});
