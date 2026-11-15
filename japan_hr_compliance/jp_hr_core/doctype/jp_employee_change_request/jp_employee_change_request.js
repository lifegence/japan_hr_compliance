// 従業員の変更申請（EMP-86）。承認すると Employee に反映する
frappe.ui.form.on("JP Employee Change Request", {
	refresh(frm) {
		if (frm.doc.status !== "申請中" || frm.is_new()) return;
		frm.add_custom_button(__("承認して反映"), () => {
			frappe.call({ method: "japan_hr_compliance.jp_hr_core.change_request.approve", args: { name: frm.doc.name } })
				.then(() => frm.reload_doc());
		});
		frm.add_custom_button(__("却下"), () => {
			frappe.prompt({ fieldname: "reason", label: __("却下の理由"), fieldtype: "Small Text", reqd: 1 }, (v) => {
				frappe.call({ method: "japan_hr_compliance.jp_hr_core.change_request.reject", args: { name: frm.doc.name, reason: v.reason } })
					.then(() => frm.reload_doc());
			}, __("却下"));
		});
	},
});
