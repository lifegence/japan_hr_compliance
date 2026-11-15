// 標準報酬月額の等級表の版（SIR-03）。CSV の取込み
frappe.ui.form.on("JP Standard Remuneration Table", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("等級を CSV で取込み"), () => new frappe.ui.FileUploader({
			restrictions: { allowed_file_types: [".csv"] },
			dialog_title: __("grade,standard,lower,upper"),
			on_success: (file) => frm.call("import_grades_csv", { file_url: file.file_url }).then((r) => {
				frm.reload_doc();
				frappe.show_alert({ message: __("{0} 等級を取り込みました", [r.message]), indicator: "green" });
			}),
		}), __("CSV"));
	},
});
