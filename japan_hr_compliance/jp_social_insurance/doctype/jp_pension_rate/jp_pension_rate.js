// 厚生年金・拠出金率の版（SIR-01・06）。保険料額表との突合（5.3）
frappe.ui.form.on("JP Pension Rate", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("保険料額表と突合"), () => new frappe.ui.FileUploader({
			restrictions: { allowed_file_types: [".csv"] },
			dialog_title: __("grade,pension_half"),
			on_success: (file) => frm.call("reconcile_premium_table", { file_url: file.file_url }).then((r) => {
				frm.reload_doc();
				frappe.show_alert({ message: __("不一致 {0} 件", [r.message]), indicator: "green" });
			}),
		}), __("CSV"));
		frm.add_custom_button(__("この版を使った給与明細"), () => frm.call("get_affected_salary_slips").then((r) => {
			const rows = (r.message || []).map((s) => `<li>${s.name}（${s.employee_name}・${s.posting_date}）</li>`).join("");
			frappe.msgprint({ title: __("影響する給与明細"), message: rows ? `<ul>${rows}</ul>` : __("ありません") });
		}));
	},
});
